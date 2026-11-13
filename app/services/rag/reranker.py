"""Cross-Encoder 精排 —— 把「大概是这条」变成「就是这条」。

粗排（向量 / BM25）是双塔结构：query 和 document 各自编码，从没互相看过。
精排把 (query, document) 成对送进模型，能读到「这条条文到底回答了问题没有」，
所以精度高一个档，代价是慢——只能对少量候选做。

因此顺序不能反：先召回 20~50 条，再精排取 top_n。
反过来（全库精排）是 8,984 次前向计算，CPU 上要跑十几分钟。

**阈值口径是概率，不是 logits**。sentence-transformers 对单标签回归头默认套了一层
sigmoid，`CrossEncoder.predict` 返回的已经在 0~1 之间：实测无关条文 ≈ 0.0000，
「房东不退押金」对民法典第 733 条 ≈ 0.46，强相关能到 0.99。

所以阈值要在概率口径上标定，0.3 只是起点；换成别的精排模型（尤其是输出 logits 的
XLM-R 原生实现）必须重新标定，否则阈值会整段失效（见 ``scripts/eval_retrieval.py``）。
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from app.core.config import settings
from app.core.logging import logger

from app.services.rag.types import LawChunk

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

_cross_encoder: Optional["CrossEncoder"] = None
_model_lock = threading.Lock()
_warned_missing = False


def _resolve_model_path() -> str:
    """解析精排模型目录的绝对路径。

    Returns:
        str: 绝对路径
    """
    configured = Path(settings.RERANKER_MODEL_PATH)
    if configured.is_absolute():
        return str(configured)
    project_root = Path(__file__).resolve().parents[3]
    return str((project_root / configured).resolve())


def model_available() -> bool:
    """精排模型是否可用。

    Returns:
        bool: 目录存在且含 ``config.json`` 时为 True
    """
    return (Path(_resolve_model_path()) / "config.json").exists()


def _get_cross_encoder() -> Optional["CrossEncoder"]:
    """加载精排模型（单例）。

    模型缺失时返回 None 而不是抛错：精排是「加分项」，缺了系统应该降级跑
    （用 RRF 顺序），而不是整个检索挂掉。降级状态会在日志与返回结果里明确标出，
    不做静默降级。

    Returns:
        Optional[CrossEncoder]: 模型实例；不可用时为 None
    """
    global _cross_encoder, _warned_missing

    if _cross_encoder is None:
        with _model_lock:
            if _cross_encoder is None:
                if not model_available():
                    if not _warned_missing:
                        logger.warning(
                            "reranker_model_missing",
                            path=_resolve_model_path(),
                            hint="精排降级为 RRF 顺序；把 bge-reranker-base 放到该目录即可启用",
                        )
                        _warned_missing = True
                    return None

                from sentence_transformers import CrossEncoder

                # max_length 默认 512，而法条平均只有 114 字（约 150 token）。
                # 截到 256 几乎不损精度，CPU 上前向计算时间直接减半。
                _cross_encoder = CrossEncoder(_resolve_model_path(), device="cpu", max_length=256)
                logger.info("reranker_model_loaded", path=_resolve_model_path())
    return _cross_encoder


def _predict_sync(query: str, chunks: list[LawChunk], top_n: int) -> list[tuple[LawChunk, float]]:
    """同步精排（内部函数，供线程池调用）。

    Args:
        query: 原始查询（用归一化后的原文，不要用改写后的）
        chunks: 候选分块
        top_n: 返回条数

    Returns:
        list[tuple[LawChunk, float]]: (分块, 精排分数) 降序
    """
    encoder = _get_cross_encoder()
    if encoder is None or not chunks:
        return []

    pairs = [(query, chunk.content) for chunk in chunks]
    scores = encoder.predict(pairs)
    ranked = sorted(zip(chunks, scores), key=lambda item: float(item[1]), reverse=True)
    return [(chunk, float(score)) for chunk, score in ranked[:top_n]]


async def rerank(query: str, chunks: list[LawChunk], top_n: Optional[int] = None) -> list[tuple[LawChunk, float]]:
    """对候选分块精排。

    Args:
        query: 查询文本
        chunks: 粗排候选
        top_n: 返回条数，默认取配置 ``RERANKER_TOP_N``

    Returns:
        list[tuple[LawChunk, float]]: 精排结果；模型不可用时返回空列表，
            调用方应退回 RRF 顺序而不是当成「无结果」
    """
    if not settings.RERANKER_ENABLED:
        return []
    if not chunks:
        return []

    limit = top_n or settings.RERANKER_TOP_N
    return await asyncio.to_thread(_predict_sync, query, chunks, limit)


async def warmup() -> None:
    """预热精排模型。"""
    if not settings.RERANKER_ENABLED:
        return
    if not model_available():
        logger.warning("reranker_warmup_skipped", reason="model_missing", path=_resolve_model_path())
        return
    await asyncio.to_thread(_get_cross_encoder)
    logger.info("reranker_warmed_up")
