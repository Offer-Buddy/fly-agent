"""BM25 关键词检索 —— 补向量检索的短板。

纯向量检索在法条场景有两个稳定的失败点：

1. **精确条号**：「第358条」的语义和「建设用地使用权提前收回」不是一回事，
   向量可能召回一堆相邻条款；BM25 靠字面重叠能一击命中
2. **法规名 + 短词**：「刑法 盗窃」这种两三个词的查询，向量容易漂移到相关但
   不对的罪名上

所以关键词这一路不是备胎，是必需项。

**为什么放内存不放 PG**：8,984 条的规模上，内存 BM25 查询 48~97ms 且稳定；
PG 的全文检索要先解决中文分词（内置分词器把整句当一个 token），
改 unigram+bigram 后服务端耗时 671~3,189ms，反而更慢。PG 的 ``tsv`` 列
仍然保留，作为以后要「一条 SQL 查完两路」的预留位。
"""

from __future__ import annotations

import asyncio
import re
from typing import Optional

from rank_bm25 import BM25Okapi

from app.services.rag.types import LawChunk

# 中文连续串，或英文/数字串
_TOKEN_PATTERN = re.compile(r"[一-鿿]+|[a-zA-Z0-9]+")


def tokenize(text: str) -> list[str]:
    """中文分词 —— unigram + bigram 混切。

    没有引入 jieba，原因很实际：法律术语的词典外词太多（「居住权」「保理合同」
    都是民法典新造词），通用分词器会切错；而 unigram+bigram 的组合在检索任务上
    召回并不差，还省掉一个词典依赖与加载时间。

    Args:
        text: 原文

    Returns:
        list[str]: token 列表。中文切成单字 + 相邻双字，英文数字整体小写
    """
    tokens: list[str] = []
    for segment in _TOKEN_PATTERN.findall(text):
        if segment.isascii():
            tokens.append(segment.lower())
            continue
        tokens.extend(segment)
        tokens.extend(segment[index : index + 2] for index in range(len(segment) - 1))
    return tokens


class Bm25Index:
    """内存 BM25 索引。

    Attributes:
        _chunks: 与倒排索引下标一一对应的分块列表
        _bm25: rank_bm25 的索引实例；语料为空时为 None
    """

    def __init__(self, chunks: Optional[list[LawChunk]] = None):
        """构建索引。

        Args:
            chunks: 分块列表；为空时索引处于未就绪状态，检索返回空列表
        """
        self._chunks: list[LawChunk] = []
        self._bm25: Optional[BM25Okapi] = None
        if chunks:
            self.build(chunks)

    @property
    def size(self) -> int:
        """索引里的分块数量。"""
        return len(self._chunks)

    def build(self, chunks: list[LawChunk]) -> None:
        """用全量分块重建索引。

        Args:
            chunks: 分块列表
        """
        self._chunks = chunks
        corpus = [tokenize(chunk.content) for chunk in chunks]
        # 空语料会让 BM25Okapi 除以 0，这里直接留空
        self._bm25 = BM25Okapi(corpus) if corpus else None

    def search(self, query: str, top_k: int = 20) -> list[tuple[LawChunk, float]]:
        """检索。

        Args:
            query: 查询文本
            top_k: 返回条数

        Returns:
            list[tuple[LawChunk, float]]: (分块, BM25 分数) 按分数降序；
                只返回分数大于 0 的项（BM25 的 IDF 在极短文档上可能为负）
        """
        if self._bm25 is None or not self._chunks:
            return []

        scores = self._bm25.get_scores(tokenize(query))
        ranked = sorted(enumerate(scores), key=lambda item: item[1], reverse=True)[:top_k]
        return [(self._chunks[index], float(score)) for index, score in ranked if score > 0]

    async def asearch(self, query: str, top_k: int = 20) -> list[tuple[LawChunk, float]]:
        """异步检索：打分是纯 CPU 计算，扔线程池，别堵事件循环。

        Args:
            query: 查询文本
            top_k: 返回条数

        Returns:
            list[tuple[LawChunk, float]]: 检索结果
        """
        return await asyncio.to_thread(self.search, query, top_k)
