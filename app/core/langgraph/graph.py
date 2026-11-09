"""LangGraph 智能体工作流：图的构建、状态流转与 LLM 交互。

整张图只有两个节点，构成最小可用的 ReAct 循环：

    chat（调模型）
      ├─ 模型要调工具 → tool_call → 回到 chat
      └─ 不需要工具   → END

会话历史与「人类确认」的断点续跑都依赖 Postgres 检查点，
因此检查点不可用时不降级、直接报错——静默降级会丢数据。
"""

import asyncio
from typing import (
    AsyncGenerator,
    Optional,
)
from urllib.parse import quote_plus

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    ToolMessage,
    convert_to_openai_messages,
)
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.errors import GraphInterrupt
from langgraph.graph import (
    END,
    StateGraph,
)
from langchain_core.runnables.config import RunnableConfig
from langgraph.graph.state import (
    Command,
    CompiledStateGraph,
)
from langgraph.types import (
    RetryPolicy,
    StateSnapshot,
)
from psycopg import (
    AsyncConnection,
    sql,
)
from psycopg.rows import (
    DictRow,
    dict_row,
)
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.langgraph.tools import tools
from app.core.logging import logger
from app.core.observability import langfuse_callback_handler
from app.core.prompts import load_system_prompt
from app.schemas import (
    GraphState,
    Message,
)
from app.services.llm import llm_service
from app.services.memory import memory_service
from app.utils import (
    dump_messages,
    extract_text_content,
    prepare_messages,
    process_llm_response,
)
from app.utils.tasks import create_background_task

PostgresConnPool = AsyncConnectionPool[AsyncConnection[DictRow]]


class LangGraphAgent:
    """LangGraph 智能体的门面。

    对外只暴露几个方法：取一次响应、取流式响应、取历史、清历史；
    内部负责图构建、连接池管理与响应后处理。
    """

    def __init__(self):
        """初始化智能体：绑定工具、准备连接池与图的占位。"""
        # 复用全局 LLM 服务，并把可用工具绑上去
        self.llm_service = llm_service
        self.llm_service.bind_tools(tools)
        self.tools_by_name = {tool.name: tool for tool in tools}
        self._connection_pool: Optional[PostgresConnPool] = None
        # 图编译较重，首次使用时才构建（懒加载）
        self._graph: Optional[CompiledStateGraph] = None
        logger.info(
            "langgraph_agent_initialized",
            model=settings.DEFAULT_LLM_MODEL,
            environment=settings.ENVIRONMENT.value,
        )

    async def _get_connection_pool(self) -> PostgresConnPool:
        """获取（必要时创建）PostgreSQL 连接池。

        Returns:
            AsyncConnectionPool: 已打开的连接池

        Raises:
            Exception: 连接池创建失败时，任何环境都不降级
        """
        if self._connection_pool is None:
            try:
                # 池大小由配置决定，默认可覆盖并发上限
                max_size = settings.POSTGRES_POOL_SIZE

                connection_url = (
                    "postgresql://"
                    f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
                )

                self._connection_pool = AsyncConnectionPool(
                    connection_url,
                    open=False,
                    max_size=max_size,
                    kwargs={
                        "autocommit": True,
                        "connect_timeout": 5,
                        # 关闭服务端预编译语句缓存，兼容 PgBouncer 等连接池中间件
                        "prepare_threshold": None,
                        "row_factory": dict_row,
                    },
                )
                await self._connection_pool.open()
                logger.info("connection_pool_created", max_size=max_size, environment=settings.ENVIRONMENT.value)
            except Exception as e:
                logger.exception(
                    "connection_pool_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value
                )
                # 绝不静默降级：检查点是会话历史与 HITL 续跑状态的唯一存储，
                # 没有它硬撑着服务等于丢数据，不如直接暴露故障。
                raise e
        return self._connection_pool

    async def _chat(self, state: GraphState, config: RunnableConfig) -> Command:
        """读取会话状态，调用 LLM 生成一条回复。

        Args:
            state: 当前会话状态
            config: 本次调用的运行配置（含 thread_id、metadata）

        Returns:
            Command: 携带新消息与下一个节点的指令
        """
        # 取当前生效的 LLM 实例，仅用于指标打点
        current_llm = self.llm_service.get_llm()
        model_name = (
            current_llm.model_name
            if current_llm and hasattr(current_llm, "model_name")
            else settings.DEFAULT_LLM_MODEL
        )

        username = config.get("metadata", {}).get("username")
        thread_id = config.get("configurable", {}).get("thread_id")
        SYSTEM_PROMPT = load_system_prompt(username=username, long_term_memory=state.long_term_memory)

        # 把系统提示拼到消息列表最前面
        messages = prepare_messages(state.messages, SYSTEM_PROMPT)

        try:
            # 由 LLM 服务负责重试与多模型轮转
            response_message = await self.llm_service.call(dump_messages(messages))

            # 规整结构化内容块（部分供应商会返回 list 形式的 content）
            response_message = process_llm_response(response_message)

            logger.info(
                "llm_response_generated",
                session_id=thread_id,
                model=model_name,
                environment=settings.ENVIRONMENT.value,
            )

            # 模型要调工具就去 tool_call，否则结束
            if isinstance(response_message, AIMessage) and response_message.tool_calls:
                goto = "tool_call"
            else:
                goto = END

            return Command(update={"messages": [response_message]}, goto=goto)
        except Exception as e:
            logger.error(
                "llm_call_failed_all_models",
                session_id=thread_id,
                error=str(e),
                environment=settings.ENVIRONMENT.value,
            )
            raise Exception(f"failed to get llm response after trying all models: {str(e)}")

    # 工具节点
    async def _tool_call(self, state: GraphState) -> Command:
        """执行上一条消息里的工具调用。

        Args:
            state: 含消息与工具调用的当前状态

        Returns:
            Command: 携带工具结果并回到 chat 节点的指令
        """
        tool_calls = state.messages[-1].tool_calls

        async def _execute_tool(tool_call: dict) -> ToolMessage:
            """执行单个工具调用，并把结果包成 ToolMessage。"""
            tool_result = await self.tools_by_name[tool_call["name"]].ainvoke(tool_call["args"])
            return ToolMessage(
                content=tool_result,
                name=tool_call["name"],
                tool_call_id=tool_call["id"],
            )

        # 模型一次要求多个工具时并发执行，避免串行等待
        if len(tool_calls) == 1:
            outputs = [await _execute_tool(tool_calls[0])]
        else:
            outputs = list(await asyncio.gather(*[_execute_tool(tc) for tc in tool_calls]))

        return Command(update={"messages": outputs}, goto="chat")

    async def create_graph(self) -> CompiledStateGraph:
        """构建并编译 LangGraph 工作流。

        Returns:
            CompiledStateGraph: 编译好的图，始终带检查点

        Raises:
            Exception: 图构建失败时，任何环境都不降级
        """
        if self._graph is None:
            try:
                graph_builder = StateGraph(GraphState)
                graph_builder.add_node("chat", self._chat, destinations=("tool_call", END))
                graph_builder.add_node(
                    "tool_call",
                    self._tool_call,
                    destinations=("chat",),
                    # 工具偶发失败（如搜索超时）重试三次
                    retry_policy=RetryPolicy(max_attempts=3),
                )
                graph_builder.set_entry_point("chat")
                graph_builder.set_finish_point("chat")

                # 连接池创建失败会抛错——没有检查点就不提供服务
                connection_pool = await self._get_connection_pool()
                checkpointer = AsyncPostgresSaver(connection_pool)
                await checkpointer.setup()

                self._graph = graph_builder.compile(
                    checkpointer=checkpointer, name=f"{settings.PROJECT_NAME} Agent ({settings.ENVIRONMENT.value})"
                )

                logger.info(
                    "graph_created",
                    graph_name=f"{settings.PROJECT_NAME} Agent",
                    environment=settings.ENVIRONMENT.value,
                    has_checkpointer=checkpointer is not None,
                )
            except Exception as e:
                logger.exception("graph_creation_failed", error=str(e), environment=settings.ENVIRONMENT.value)
                raise e

        return self._graph

    async def _get_graph(self) -> CompiledStateGraph:
        """返回已编译的图，首次访问时构建。

        Raises:
            Exception: 初始化失败时由 ``create_graph()`` 原样抛出；
                调用方可以认为返回值一定非空。
        """
        if self._graph is None:
            self._graph = await self.create_graph()
        return self._graph

    async def get_response(
        self,
        messages: list[Message],
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> list[Message]:
        """非流式获取一次完整回复。

        Args:
            messages: 要发给 LLM 的消息列表
            session_id: 会话 ID（同时作为 thread_id）
            user_id: 用户 ID，用于长期记忆检索
            username: 用户显示名，会拼进系统提示

        Returns:
            list[Message]: LLM 的回复
        """
        graph = await self._get_graph()
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "callbacks": callbacks,
            # 这个值会成为 Langfuse 里 trace 的名字，不设就会叫「LangGraph」
            "run_name": "chat",
            "metadata": {
                # 业务自用键：日志与排查用
                "user_id": user_id,
                "username": username,
                "session_id": session_id,
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
                # Langfuse 只认这三个带前缀的键，写错等于会话与用户维度全是空的
                "langfuse_session_id": session_id,
                "langfuse_user_id": user_id,
                "langfuse_tags": ["chat", settings.ENVIRONMENT.value],
            },
        }

        try:
            # 状态检查与记忆检索并发跑，省掉 200~500ms 串行等待
            state, relevant_memory = await asyncio.gather(
                graph.aget_state(config),
                memory_service.search(user_id, messages[-1].content),
            )

            if state.next:
                # 上次被人类确认节点打断，这里带着用户回答继续跑
                logger.info("resuming_interrupted_graph", session_id=session_id, next_nodes=state.next)
                response = await graph.ainvoke(
                    Command(resume=messages[-1].content),
                    config=config,
                )
            else:
                relevant_memory = relevant_memory or "No relevant memory found."
                response = await graph.ainvoke(
                    input={"messages": dump_messages(messages), "long_term_memory": relevant_memory},
                    config=config,
                )

            # 本轮是否又停在人类确认点
            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                logger.info("graph_interrupted", session_id=session_id, interrupt_value=str(interrupt_value))
                return [Message(role="assistant", content=str(interrupt_value))]

            # 记忆写入放后台，不阻塞响应
            create_background_task(memory_service.add(user_id, response["messages"]))
            return self.__process_messages(response["messages"])
        except GraphInterrupt:
            # LangGraph 以异常形式抛出打断，需要读出待确认内容返回给前端
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            logger.info("graph_interrupted", session_id=session_id, interrupt_value=str(interrupt_value))
            return [Message(role="assistant", content=str(interrupt_value))]
        except Exception as e:
            logger.exception("get_response_failed", error=str(e), session_id=session_id)
            raise

    async def get_stream_response(
        self,
        messages: list[Message],
        session_id: str,
        user_id: Optional[str] = None,
        username: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """流式获取回复，逐段 yield 文本增量。

        Args:
            messages: 要发给 LLM 的消息列表
            session_id: 会话 ID（同时作为 thread_id）
            user_id: 用户 ID，用于长期记忆检索
            username: 用户显示名，会拼进系统提示

        Yields:
            str: LLM 回复的文本片段
        """
        callbacks: list[BaseCallbackHandler] = [langfuse_callback_handler] if settings.LANGFUSE_TRACING_ENABLED else []
        config: RunnableConfig = {
            "configurable": {"thread_id": session_id},
            "callbacks": callbacks,
            # 这个值会成为 Langfuse 里 trace 的名字，不设就会叫「LangGraph」
            "run_name": "chat",
            "metadata": {
                # 业务自用键：日志与排查用
                "user_id": user_id,
                "username": username,
                "session_id": session_id,
                "environment": settings.ENVIRONMENT.value,
                "debug": settings.DEBUG,
                # Langfuse 只认这三个带前缀的键，写错等于会话与用户维度全是空的
                "langfuse_session_id": session_id,
                "langfuse_user_id": user_id,
                "langfuse_tags": ["chat", settings.ENVIRONMENT.value],
            },
        }
        graph = await self._get_graph()

        try:
            # 同非流式：状态检查与记忆检索并发
            state, relevant_memory = await asyncio.gather(
                graph.aget_state(config),
                memory_service.search(user_id, messages[-1].content),
            )

            if state.next:
                logger.info("resuming_interrupted_graph_stream", session_id=session_id, next_nodes=state.next)
                graph_input = Command(resume=messages[-1].content)
            else:
                relevant_memory = relevant_memory or "No relevant memory found."
                graph_input = {"messages": dump_messages(messages), "long_term_memory": relevant_memory}

            async for token, _ in graph.astream(
                graph_input,
                config,
                stream_mode="messages",
            ):
                # 只透传助手消息，工具消息与状态更新不外发
                if not isinstance(token, (AIMessage, AIMessageChunk)):
                    continue

                text = extract_text_content(token.content)
                if text:
                    yield text

            # 流结束后再判断是否停在人类确认点，或该写记忆了
            state = await graph.aget_state(config)
            if state.next:
                interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
                logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
                yield str(interrupt_value)
            elif state.values and "messages" in state.values:
                create_background_task(memory_service.add(user_id, state.values["messages"]))
        except GraphInterrupt:
            state = await graph.aget_state(config)
            interrupt_value = state.tasks[0].interrupts[0].value if state.tasks else "Waiting for input."
            logger.info("graph_interrupted_stream", session_id=session_id, interrupt_value=str(interrupt_value))
            yield str(interrupt_value)
        except Exception as stream_error:
            logger.exception("stream_processing_failed", error=str(stream_error), session_id=session_id)
            raise stream_error

    async def get_chat_history(self, session_id: str) -> list[Message]:
        """读取指定会话的历史消息。

        Args:
            session_id: 会话 ID（thread_id）

        Returns:
            list[Message]: 历史消息（仅用户与助手）
        """
        graph = await self._get_graph()

        config: RunnableConfig = {"configurable": {"thread_id": session_id}}
        state: StateSnapshot = await graph.aget_state(config=config)
        return self.__process_messages(state.values["messages"]) if state.values else []

    def __process_messages(self, messages: list[BaseMessage]) -> list[Message]:
        """把内部消息转换成对外暴露的 user/assistant 两种角色。"""
        openai_style_messages = convert_to_openai_messages(messages)
        # 只保留用户与助手消息，工具消息对前端不可见
        return [
            Message(role=message["role"], content=str(message["content"]))
            for message in openai_style_messages
            if message["role"] in ["assistant", "user"] and message["content"]
        ]

    async def clear_chat_history(self, session_id: str) -> None:
        """清空指定会话的全部聊天历史。

        Args:
            session_id: 要清理的会话 ID

        Raises:
            Exception: 清理过程中任何失败都会向上抛出
        """
        try:
            # 确保连接池在当前事件循环里已初始化
            conn_pool = await self._get_connection_pool()
            if conn_pool is None:
                raise RuntimeError("connection pool unavailable; cannot clear chat history")

            # 三张检查点表的 DELETE 放进同一个 pipeline，只走一次网络往返
            async with conn_pool.connection() as conn:
                async with conn.pipeline():
                    for table in settings.CHECKPOINT_TABLES:
                        await conn.execute(
                            sql.SQL("DELETE FROM {} WHERE thread_id = %s").format(sql.Identifier(table)),
                            (session_id,),
                        )
                logger.info(
                    "checkpoint_tables_cleared_for_session",
                    tables=settings.CHECKPOINT_TABLES,
                    session_id=session_id,
                )

        except Exception as e:
            logger.error(
                "clear_chat_history_operation_failed",
                session_id=session_id,
                error=str(e),
            )
            raise
