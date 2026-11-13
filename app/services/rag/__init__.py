"""法律知识库（RAG）—— 对外只暴露「拿到一个能用的检索器」。

内部拆成 7 个模块，各有单一职责：

    types      数据结构（LawChunk / LawMeta）
    normalize  条号归一化与法名猜测
    chunker    条款级分块
    embedder   bge-small-zh 向量化
    keyword    内存 BM25
    pg_store   pgvector 读写
    reranker   Cross-Encoder 精排
    hybrid     粗排 + 融合 + 精排的编排

**为什么要这个门面**：BM25 索引要把全量分块读进内存（9k 条、约 20MB），
不能让每个请求自己做；但也不能在 import 时就做——那会让「只是导入模块」
变成一个 2 秒的操作。所以统一走懒加载单例，配一个可选预热。
"""

from __future__ import annotations

import asyncio
from typing import Optional

from app.core.config import settings
from app.core.logging import logger

from app.services.rag import embedder
from app.services.rag.hybrid import (
    HybridRetriever,
    RetrievalResult,
    reranker_ready,
)
from app.services.rag.keyword import Bm25Index
from app.services.rag.pg_store import law_store
from app.services.rag.types import (
    LawChunk,
    LawMeta,
)

__all__ = [
    "HybridRetriever",
    "LawChunk",
    "LawMeta",
    "RetrievalResult",
    "close_retriever",
    "get_retriever",
    "is_enabled",
    "reset_retriever",
    "warmup",
]

_retriever: Optional[HybridRetriever] = None
_lock = asyncio.Lock()


def is_enabled() -> bool:
    """法律检索是否启用。

    Returns:
        bool: 配置 ``RAG_ENABLED`` 为真时为 True
    """
    return settings.RAG_ENABLED


async def _build() -> HybridRetriever:
    """装配检索器：读全量分块 → 建 BM25 → 拿法规名单。

    Returns:
        HybridRetriever: 可用的检索器
    """
    chunks = await law_store.fetch_all_chunks()
    # 建索引是纯 CPU 活（9k 条分词 + IDF 统计，约 1~2 秒），必须离开事件循环
    bm25 = await asyncio.to_thread(Bm25Index, chunks) if chunks else None
    law_names = await law_store.list_law_names()
    logger.info(
        "law_retriever_ready",
        chunks=len(chunks),
        laws=len(law_names),
        bm25=bm25.size if bm25 else 0,
        reranker=reranker_ready(),
    )
    return HybridRetriever(store=law_store, bm25=bm25, law_names=law_names)


async def get_retriever() -> HybridRetriever:
    """获取检索器单例（首次调用时装配）。

    Returns:
        HybridRetriever: 检索器
    """
    global _retriever
    if _retriever is None:
        async with _lock:
            if _retriever is None:
                _retriever = await _build()
    return _retriever


async def warmup() -> None:
    """预热全套组件：向量模型、精排模型、BM25 索引。

    在服务启动时调用。不预热的话，第一个真实请求要多等 3~5 秒
    （两个模型加载 + 索引构建），很容易被误判成「检索很慢」。
    """
    if not is_enabled():
        logger.info("law_rag_disabled")
        return

    await asyncio.gather(embedder.warmup(), _warmup_reranker(), return_exceptions=True)
    await get_retriever()
    logger.info("law_rag_warmed_up")


async def _warmup_reranker() -> None:
    """预热精排模型（内部函数，异常不向上抛）。"""
    from app.services.rag import reranker

    await reranker.warmup()


async def close_retriever() -> None:
    """关闭存储连接池，重置单例（服务关停时调用）。"""
    global _retriever
    _retriever = None
    await law_store.close()


def reset_retriever() -> None:
    """重置单例（仅供测试使用）。"""
    global _retriever
    _retriever = None
