"""检索分阶段诊断 —— 定位「错在哪一步」。

一条链路有四段，任何一段漏了，最后都是「答错」：

    向量召回 topK → 关键词召回 topK → RRF 融合 → 精排 topN

只看 hit@5 是查不出原因的。这个脚本对每条评测样本，分别算标注条文在
**每一段的排名**，最后给出各段的覆盖率：

- 向量段就丢了 → embedding 模型或语料有问题（换模型 / 检查分块）
- 关键词段补上了 → 说明该加 BM25（本项目的默认配置已加）
- 融合后还在、精排后没了 → 精排模型太重，或候选池太小
- 四段都在、排在第 6 名 → 只要把 top_n 调大就能捞回来

用法：

    python scripts/diagnose_retrieval.py --limit 30
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.services import rag  # noqa: E402
from app.services.rag import embedder  # noqa: E402
from app.services.rag.normalize import (  # noqa: E402
    extract_query_articles,
    guess_law_names,
    normalize_query,
)
from app.services.rag.reranker import rerank as rerank_chunks  # noqa: E402


def parse_args() -> argparse.Namespace:
    """解析参数。

    Returns:
        argparse.Namespace: 参数
    """
    parser = argparse.ArgumentParser(description="检索分阶段诊断")
    parser.add_argument("--dataset", default="data/eval/legal_qa.json")
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--top-k", type=int, default=20, help="每路召回条数（与 RETRIEVER_TOP_K 对齐）")
    parser.add_argument("--top-n", type=int, default=5, help="精排后保留条数")
    return parser.parse_args()


def first_rank(chunk_ids: list[str], gold: set[str]) -> int | None:
    """返回第一条命中的名次。

    Args:
        chunk_ids: 有序的分块 id
        gold: 标注集合

    Returns:
        int | None: 1 起的名次；未命中为 None
    """
    return next((position for position, chunk_id in enumerate(chunk_ids, start=1) if chunk_id in gold), None)


async def main() -> None:
    """逐阶段统计覆盖率。"""
    args = parse_args()
    samples = __import__("json").loads((PROJECT_ROOT / args.dataset).read_text(encoding="utf-8"))[: args.limit]

    retriever = await rag.get_retriever()
    stages = ["向量", "关键词", "融合", "精排"]
    stage_hits = {stage: 0 for stage in stages}
    stage_ranks: dict[str, list[int]] = {stage: [] for stage in stages}
    rows: list[dict] = []

    for sample in samples:
        gold = set(sample.get("ground_truth_contexts") or []) | set(sample.get("acceptable_contexts") or [])
        query = normalize_query(sample["question"])
        law_names = guess_law_names(query, [chunk.law_name for chunk in []]) or []

        embedding = await embedder.embed_query(query)
        vector_hits = await retriever._store.search_by_vector(embedding, top_k=args.top_k)
        keyword_hits = await retriever._keyword_recall(query)
        article_hits = await retriever._article_recall(extract_query_articles(query), law_names)
        fused = retriever._rrf_fuse([article_hits, vector_hits, keyword_hits], k=settings.RRF_K)[: args.top_k]
        reranked = await rerank_chunks(query, fused, top_n=args.top_n)

        stage_lists = {
            "向量": [chunk.chunk_id for chunk, _ in vector_hits],
            "关键词": [chunk.chunk_id for chunk, _ in keyword_hits],
            "融合": [chunk.chunk_id for chunk in fused],
            "精排": [chunk.chunk_id for chunk, _ in reranked],
        }

        row = {"question": sample["question"], "gold": sorted(gold)}
        for stage, chunk_ids in stage_lists.items():
            rank = first_rank(chunk_ids, gold)
            row[stage] = rank
            if rank:
                stage_hits[stage] += 1
                stage_ranks[stage].append(rank)
        rows.append(row)

    total = len(samples)
    print("\n" + "=" * 60)
    print(f"分阶段覆盖率　样本 {total} 条　每路 top{args.top_k}　精排保留 top{args.top_n}")
    print("=" * 60)
    for stage in stages:
        mean_rank = statistics.fmean(stage_ranks[stage]) if stage_ranks[stage] else 0
        print(f"{stage:<6}{stage_hits[stage]:>4}/{total}　{stage_hits[stage] / total:>7.1%}　平均名次 {mean_rank:.1f}")
    print("=" * 60)

    print("\n融合有、精排丢的样本（说明精排把对的压下去了）：")
    lost = [row for row in rows if row["融合"] and not row["精排"]]
    for row in lost[:10]:
        print(f"  · {row['question'][:30]}　gold={row['gold'][:2]}　融合第 {row['融合']} 名")
    if not lost:
        print("  （无）")

    print("\n四段都没召回的样本（说明语料或表达差异大）：")
    missing = [row for row in rows if not row["融合"]]
    for row in missing[:10]:
        print(f"  · {row['question'][:30]}　gold={row['gold'][:2]}")
    if not missing:
        print("  （无）")

    await rag.close_retriever()


if __name__ == "__main__":
    asyncio.run(main())
