"""长期记忆服务：mem0 + pgvector，前面再套一层缓存。

记忆的写入是异步的（不阻塞对话响应），读取则先查缓存、未命中再查向量库。
匿名会话（无 user_id）直接跳过长期记忆，避免所有匿名用户挤进同一个分区。
"""

from langchain_core.messages import BaseMessage
from mem0 import AsyncMemory

from app.core.cache import (
    cache_key,
    cache_service,
)
from app.core.config import settings
from app.core.logging import logger
from app.core.prompts import load_memory_capture_prompt
from app.utils import latest_user_text


class MemoryService:
    """基于 mem0 与 pgvector 的长期记忆服务。"""

    def __init__(self):
        """初始化记忆服务（此时尚未建立连接）。"""
        self._memory: AsyncMemory | None = None

    async def _get_memory(self) -> AsyncMemory:
        """获取（首次调用时构建）mem0 的 AsyncMemory 实例。

        向量模型允许挂在和对话模型不同的 OpenAI 兼容端点上：
        有些供应商（如 DeepSeek）没有 embeddings 接口。

        Returns:
            AsyncMemory: mem0 记忆客户端
        """
        if self._memory is None:
            embedder_config: dict = {
                "model": settings.LONG_TERM_MEMORY_EMBEDDER_MODEL,
                "embedding_dims": settings.LONG_TERM_MEMORY_EMBEDDING_DIMS,
            }
            # 向量模型可以走独立端点（部分供应商不提供 embeddings 接口）
            if settings.LONG_TERM_MEMORY_EMBEDDER_BASE_URL:
                embedder_config["openai_base_url"] = settings.LONG_TERM_MEMORY_EMBEDDER_BASE_URL
            if settings.LONG_TERM_MEMORY_EMBEDDER_API_KEY:
                embedder_config["api_key"] = settings.LONG_TERM_MEMORY_EMBEDDER_API_KEY

            self._memory = await AsyncMemory.from_config(
                config_dict={
                    "vector_store": {
                        "provider": "pgvector",
                        "config": {
                            "collection_name": settings.LONG_TERM_MEMORY_COLLECTION_NAME,
                            "dbname": settings.POSTGRES_DB,
                            "user": settings.POSTGRES_USER,
                            "password": settings.POSTGRES_PASSWORD,
                            "host": settings.POSTGRES_HOST,
                            "port": settings.POSTGRES_PORT,
                            "embedding_model_dims": settings.LONG_TERM_MEMORY_EMBEDDING_DIMS,
                        },
                    },
                    "llm": {
                        "provider": "openai",
                        "config": {"model": settings.LONG_TERM_MEMORY_MODEL},
                    },
                    "embedder": {
                        "provider": "openai",
                        "config": embedder_config,
                    },
                    # 自定义「事实抽取」提示词，约束 mem0 提取什么、以什么格式写
                    "custom_fact_extraction_prompt": load_memory_capture_prompt(),
                }
            )
        return self._memory

    async def initialize(self) -> None:
        """预热 mem0 实例与 pgvector 连接池。

        在应用启动时调用一次，避免首次 search() / add() 多付
        约 130ms 的 from_config + 表结构检查开销。
        """
        await self._get_memory()
        logger.info("memory_service_initialized")

    async def search(self, user_id: str | None, query: str) -> str:
        """检索某个用户的相关记忆。

        先查缓存，未命中再查 mem0，并把结果写回缓存。

        Args:
            user_id: 用户 ID；为 ``None`` 时直接返回空串
            query: 检索用的查询文本

        Returns:
            格式化后的记忆字符串；失败或匿名会话返回空串
        """
        if user_id is None:
            return ""
        try:
            # 先查缓存
            key = cache_key("memory", str(user_id), query)
            cached = await cache_service.get(key)
            if cached is not None:
                logger.debug("memory_search_cache_hit", user_id=user_id)
                return cached

            memory = await self._get_memory()
            results = await memory.search(user_id=str(user_id), query=query)
            result = "\n".join([f"* {r['memory']}" for r in results["results"]])

            # 只缓存成功且非空的检索结果
            if result:
                await cache_service.set(key, result)

            return result
        except Exception as e:
            logger.error("failed_to_get_relevant_memory", error=str(e), user_id=user_id, query=query)
            return ""

    async def add(self, user_id: str | None, messages: list[BaseMessage]) -> None:
        """把最近一轮用户发言写入长期记忆。

        只取最后一条用户消息。助手回复被刻意排除：mem0 的抽取器会把两个角色
        的内容拼在一起，把助手的建议当成用户自己说过的偏好，产出噪声事实。
        ``user_id`` 为 ``None`` 时直接跳过（原因见 ``search``）。

        Args:
            user_id: 用户 ID
            messages: 本轮对话的全部消息
        """
        if user_id is None:
            return
        user_text = latest_user_text(messages).strip()
        if not user_text:
            logger.debug("long_term_memory_skipped_empty_turn", user_id=user_id)
            return

        try:
            memory = await self._get_memory()
            await memory.add(
                [{"role": "user", "content": user_text}],
                user_id=str(user_id),
            )
            logger.info("long_term_memory_updated_successfully", user_id=user_id)
        except Exception as e:
            logger.exception("failed_to_update_long_term_memory", user_id=user_id, error=str(e))


memory_service = MemoryService()
