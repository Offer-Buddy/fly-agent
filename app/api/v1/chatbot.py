"""聊天接口。

提供四条路由：普通对话、流式对话、拉取历史消息、清空历史。
流式接口用 SSE（``text/event-stream``），每帧是一个 JSON，
最后一帧 ``done=true`` 表示结束。

注意：返回体里的文案（如 ``{"message": "..."}``）属于对外契约，保持英文。
"""

import json

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import StreamingResponse

from app.api.v1.auth import get_current_session
from app.core.config import settings
from app.core.langgraph.graph import LangGraphAgent
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.metrics import llm_stream_duration_seconds
from app.core.observability import trace_attributes
from app.models.session import Session
from app.schemas.chat import (
    ChatRequest,
    ChatResponse,
    StreamResponse,
)
from app.services.session_naming import maybe_name_session

router = APIRouter()
# 模块级单例：图与连接池都挂在它身上，不能每个请求新建
agent = LangGraphAgent()


@router.post("/chat", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat"][0])
async def chat(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """处理一次非流式对话请求。

    Args:
        request: FastAPI 请求对象，限流器需要
        chat_request: 请求体，含消息列表
        session: 从 token 解析出的当前会话

    Returns:
        ChatResponse: 本轮对话产生的消息

    Raises:
        HTTPException: 处理过程中出现异常
    """
    try:
        logger.info(
            "chat_request_received",
            session_id=session.id,
            message_count=len(chat_request.messages),
        )

        # 首轮对话时用 LLM 生成会话标题（后台任务，不阻塞）
        if settings.SESSION_NAMING_ENABLED:
            maybe_name_session(session.id, session.name, chat_request.messages)

        # 在请求入口挂一次追踪属性，图里所有 LLM 调用与工具调用都会继承
        with trace_attributes(session_id=session.id, user_id=str(session.user_id), tags=["chat"]):
            result = await agent.get_response(
                chat_request.messages, session.id, user_id=str(session.user_id), username=session.username
            )

        logger.info("chat_request_processed", session_id=session.id)

        return ChatResponse(messages=result)
    except Exception as e:
        logger.exception("chat_request_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/stream")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["chat_stream"][0])
async def chat_stream(
    request: Request,
    chat_request: ChatRequest,
    session: Session = Depends(get_current_session),
):
    """处理一次流式对话请求，以 SSE 逐段下发。

    Args:
        request: FastAPI 请求对象，限流器需要
        chat_request: 请求体，含消息列表
        session: 从 token 解析出的当前会话

    Returns:
        StreamingResponse: SSE 流式响应

    Raises:
        HTTPException: 建立流之前就出错时抛出
    """
    try:
        logger.info(
            "stream_chat_request_received",
            session_id=session.id,
            message_count=len(chat_request.messages),
        )

        if settings.SESSION_NAMING_ENABLED:
            maybe_name_session(session.id, session.name, chat_request.messages)

        async def event_generator():
            r"""逐帧产出 SSE 事件。

            Yields:
                str: 形如 ``data: {...}\n\n`` 的事件帧

            Raises:
                Exception: 流式处理中出现异常
            """
            try:
                with llm_stream_duration_seconds.labels(model=agent.llm_service.get_llm().get_name()).time():
                    async for chunk in agent.get_stream_response(
                        chat_request.messages, session.id, user_id=str(session.user_id), username=session.username
                    ):
                        response = StreamResponse(content=chunk, done=False)
                        yield f"data: {json.dumps(response.model_dump(mode='json'))}\n\n"

                # 结束帧：告诉前端可以停止读取
                final_response = StreamResponse(content="", done=True)
                yield f"data: {json.dumps(final_response.model_dump(mode='json'))}\n\n"

            except Exception as e:
                # 流已开始，无法再改 HTTP 状态码，只能把错误塞进最后一帧
                logger.exception(
                    "stream_chat_request_failed",
                    session_id=session.id,
                    error=str(e),
                )
                error_response = StreamResponse(content=str(e), done=True)
                yield f"data: {json.dumps(error_response.model_dump(mode='json'))}\n\n"

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    except Exception as e:
        logger.exception(
            "stream_chat_request_failed",
            session_id=session.id,
            error=str(e),
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/messages", response_model=ChatResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def get_session_messages(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """拉取当前会话的全部消息。

    Args:
        request: FastAPI 请求对象，限流器需要
        session: 从 token 解析出的当前会话

    Returns:
        ChatResponse: 会话内的历史消息

    Raises:
        HTTPException: 读取失败
    """
    try:
        messages = await agent.get_chat_history(session.id)
        return ChatResponse(messages=messages)
    except Exception as e:
        logger.exception("get_messages_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/messages")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["messages"][0])
async def clear_chat_history(
    request: Request,
    session: Session = Depends(get_current_session),
):
    """清空当前会话的全部消息。

    Args:
        request: FastAPI 请求对象，限流器需要
        session: 从 token 解析出的当前会话

    Returns:
        dict: 清理结果提示
    """
    try:
        await agent.clear_chat_history(session.id)
        return {"message": "Chat history cleared successfully"}
    except Exception as e:
        logger.exception("clear_chat_history_failed", session_id=session.id, error=str(e))
        raise HTTPException(status_code=500, detail=str(e))
