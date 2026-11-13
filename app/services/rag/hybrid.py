"""混合检索 —— 向量 + 关键词 + 精确条号，RRF 融合后精排。

流水线长这样，每一步都有它不可替代的理由：

    归一化条号
      ├─ 向量召回 top20    语义相近：用户说「房东不退押金」也能找到「租赁保证金」
      ├─ BM25 召回 top20   字面命中：「第358条」「刑法 盗窃」
      └─ 条号精确命中      确定性：说了第几条就必须给第几条，不赌近似
            ↓ RRF 融合（k=60）
            ↓ Cross-Encoder 精排 top5
            ↓ 阈值过滤（可选，分数低就明确告诉模型「没查到」）

**RRF 而不是加权求和**：两路分数量纲完全不同（余弦 0~1，BM25 无上界），
加权求和要先归一化，而归一化参数会随查询漂移。RRF 只用名次，天然免疫量纲问题，
k=60 是原论文的经验值，作用是压平头部名次的差距。

**不做 HyDE**：Legal 里有「问题改写 + 假设性文档」，需要额外两次 LLM 调用，
单次检索多 1~2 秒，还引入不确定性。教学场景先把确定性搞稳，需要时再加。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from app.core.config import settings
from app.core.logging import logger
from app.services.rag import embedder
from app.services.rag.keyword import Bm25Index
from app.services.rag.normalize import (
    extract_query_articles,
    guess_law_names,
    normalize_query,
)
from app.services.rag.pg_store import PgLawStore
from app.services.rag.reranker import rerank as rerank_chunks
from app.services.rag.reranker import model_available as reranker_available
from app.services.rag.types import LawChunk


@dataclass(slots=True)
class RetrievalResult:
    """一次检索的完整结果。

    ``status`` 是给上层（工具层、Agent）判断「要不要换个问法」的依据，
    比只返回一个列表有用得多：模型知道自己是「没查到」还是「查到但不敢信」。

    Attributes:
        query: 归一化后的查询
        chunks: 命中的分块，按相关度降序，``score`` 已回填
        reranked: 是否成功走了精排（模型缺失时为 False）
        threshold: 本次使用的精排阈值
        candidates: 进入精排的候选数
        candidate_chunks: 进入精排的候选池，RRF 顺序，``score`` 未回填。
            评测用它把「召回没捞到」和「捞到了没排上」分开；生产路径只看 ``chunks``
        timings: 各阶段耗时（毫秒），排查慢查询用
    """

    query: str
    chunks: list[LawChunk]
    reranked: bool
    threshold: float
    candidates: int
    candidate_chunks: list[LawChunk] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)

    @property
    def top_score(self) -> Optional[float]:
        """最高精排分；未精排或无结果时为 None。"""
        if not self.reranked or not self.chunks:
            return None
        return self.chunks[0].score

    @property
    def status(self) -> str:
        """检索质量状态。

        Returns:
            str: ``found`` 可直接引用 / ``low_quality`` 分数低于阈值 /
                ``no_relevant_result`` 没命中 / ``unranked`` 未精排
        """
        if not self.chunks:
            return "no_relevant_result"
        if not self.reranked:
            return "unranked"
        return "found" if (self.top_score or 0.0) >= self.threshold else "low_quality"


class HybridRetriever:
    """两阶段检索器（粗排 + 精排）。

    Attributes:
        _store: pgvector 存储
        _bm25: 内存 BM25 索引
        _law_names: 库里的法规简称，用于从查询里猜法名
    """

    def __init__(
        self,
        store: Optional[PgLawStore] = None,
        bm25: Optional[Bm25Index] = None,
        law_names: Optional[list[str]] = None,
    ):
        """初始化。

        Args:
            store: 存储实例，默认用全局单例
            bm25: 已构建的 BM25 索引；为空时关键词那一路自动跳过
            law_names: 已知法规简称
        """
        self._store = store or PgLawStore()
        self._bm25 = bm25
        self._law_names = law_names or []

    @property
    def bm25_size(self) -> int:
        """BM25 索引里的分块数，0 表示关键词那一路未启用。"""
        return self._bm25.size if self._bm25 else 0

    def _rrf_fuse(
        self,
        ranked_lists: list[list[tuple[LawChunk, float]]],
        k: int,
    ) -> list[LawChunk]:
        """RRF 融合多路召回。

        Args:
            ranked_lists: 若干已排序的召回列表，越靠前名次越高
            k: RRF 平滑常数

        Returns:
            list[LawChunk]: 融合后的分块，按 RRF 分数降序
        """
        scores: dict[str, float] = {}
        chunk_map: dict[str, LawChunk] = {}

        for results in ranked_lists:
            for rank, (chunk, _) in enumerate(results, start=1):
                scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (k + rank)
                chunk_map[chunk.chunk_id] = chunk

        ordered = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
        return [chunk_map[chunk_id] for chunk_id in ordered]

    async def retrieve(self, query: str, top_k: int = 5) -> RetrievalResult:
        """执行完整检索流水线。

        Args:
            query: 用户原始查询
            top_k: 最终返回条数

        Returns:
            RetrievalResult: 结果与质量状态
        """
        timings: dict[str, float] = {}
        started = time.perf_counter()

        normalized = normalize_query(query)
        law_names = guess_law_names(normalized, self._law_names)
        article_numbers = extract_query_articles(normalized)

        # 三路并发：编码 + 向量检索、BM25、条号精确查
        embed_started = time.perf_counter()
        embedding = await embedder.embed_query(normalized)
        timings["embed_ms"] = round((time.perf_counter() - embed_started) * 1000, 1)

        recall_started = time.perf_counter()
        vector_task = self._store.search_by_vector(embedding, top_k=settings.RETRIEVER_TOP_K, law_names=law_names)
        keyword_task = self._keyword_recall(normalized)
        article_task = self._article_recall(article_numbers, law_names)
        vector_results, keyword_results, article_results = await asyncio.gather(
            vector_task, keyword_task, article_task
        )
        timings["recall_ms"] = round((time.perf_counter() - recall_started) * 1000, 1)

        fused = self._rrf_fuse(
            [article_results, vector_results, keyword_results],
            k=settings.RRF_K,
        )
        candidates = fused[: settings.RETRIEVER_TOP_K]

        if not candidates:
            timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
            logger.info("law_retrieval_empty", query=normalized, articles=article_numbers, **timings)
            return RetrievalResult(
                query=normalized,
                chunks=[],
                reranked=False,
                threshold=settings.RERANKER_SCORE_THRESHOLD,
                candidates=0,
                candidate_chunks=[],
                timings=timings,
            )

        rerank_started = time.perf_counter()
        reranked = await rerank_chunks(normalized, candidates, top_n=top_k)
        timings["rerank_ms"] = round((time.perf_counter() - rerank_started) * 1000, 1)

        if reranked:
            threshold = settings.RERANKER_SCORE_THRESHOLD
            # 阈值过滤只影响「给模型看什么」，不影响分数本身：
            # 保留一条都不剩时上层要能说清「查到了但不可信」
            kept = [(chunk, score) for chunk, score in reranked if score >= threshold]
            chunks = [self._with_score(chunk, score) for chunk, score in kept] or [
                self._with_score(chunk, score) for chunk, score in reranked[:1]
            ]
            passed = bool(kept)
            timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
            logger.info(
                "law_retrieval_done",
                query=normalized,
                candidates=len(candidates),
                returned=len(chunks),
                top_score=round(reranked[0][1], 4),
                passed_threshold=passed,
                articles=article_numbers,
                law_names=law_names,
                **timings,
            )
            return RetrievalResult(
                query=normalized,
                chunks=chunks,
                reranked=True,
                threshold=threshold,
                candidates=len(candidates),
                candidate_chunks=candidates,
                timings=timings,
            )

        # 精排不可用：退回 RRF 顺序，并明确标注 unranked，不假装有分数
        chunks = [self._with_score(chunk, 0.0) for chunk in candidates[:top_k]]
        timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
        logger.warning("law_retrieval_unranked", query=normalized, reason="reranker_unavailable_or_disabled")
        return RetrievalResult(
            query=normalized,
            chunks=chunks,
            reranked=False,
            threshold=settings.RERANKER_SCORE_THRESHOLD,
            candidates=len(candidates),
            candidate_chunks=candidates,
            timings=timings,
        )

    async def _keyword_recall(self, query: str) -> list[tuple[LawChunk, float]]:
        """关键词召回；索引未构建时返回空列表。

        Args:
            query: 归一化后的查询

        Returns:
            list[tuple[LawChunk, float]]: BM25 结果
        """
        if self._bm25 is None or self._bm25.size == 0:
            return []
        return await self._bm25.asearch(query, top_k=settings.RETRIEVER_TOP_K)

    async def _article_recall(
        self, article_numbers: list[int], law_names: list[str]
    ) -> list[tuple[LawChunk, float]]:
        """条号精确召回。

        Args:
            article_numbers: 查询里提到的条号
            law_names: 查询里猜到的法规

        Returns:
            list[tuple[LawChunk, float]]: 命中的分块，分数固定为 1.0（只用于排序占位）
        """
        if not article_numbers:
            return []

        hits: list[LawChunk] = []
        for number in article_numbers[:3]:
            hits.extend(await self._store.find_by_article(number, law_names=law_names, limit=2))
        return [(chunk, 1.0) for chunk in hits]

    @staticmethod
    def _with_score(chunk: LawChunk, score: float) -> LawChunk:
        """回填精排分数（dataclass 是可变对象，改完直接返回）。

        Args:
            chunk: 分块
            score: 分数

        Returns:
            LawChunk: 同一个对象
        """
        chunk.score = score
        return chunk


def reranker_ready() -> bool:
    """精排是否可用，供启动日志与健康检查使用。

    Returns:
        bool: 配置开启且模型存在时为 True
    """
    return settings.RERANKER_ENABLED and reranker_available()
