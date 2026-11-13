"""检索评测 —— 用 100 条真实法律问答量一遍检索到底准不准。

指标口径（都按 chunk 级判定，命中标准是 ``chunk_id`` 落在标注集合里）：

    hit@k      前 k 条里出现任意一条标注条文的样本占比
    MRR@k      第一条命中排名的倒数均值，衡量「排得多靠前」
    recall@k   标注条文被召回的比例（多标注样本才看得出差别）
    candidate_hit / candidate_recall
               金标有没有进「送进精排的候选池」。它和 hit@k 的差就是
               「召回到了但没排上」的损失——这两个数分开，才能判断该动召回还是动精排

为什么只用检索评测、不先做端到端评测：回答差可能是检索错、可能是提示词差、
也可能是模型本身不行。先量检索，才能把问题定位到层。

用法：

    python scripts/eval_retrieval.py
    python scripts/eval_retrieval.py --top-k 5 --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.logging import logger  # noqa: E402
from app.services import rag  # noqa: E402


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    Returns:
        argparse.Namespace: 参数
    """
    parser = argparse.ArgumentParser(description="法律检索评测")
    parser.add_argument("--dataset", default="data/eval/legal_qa.json", help="评测集路径")
    parser.add_argument("--top-k", type=int, default=5, help="检索条数")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 条（0 = 全部）")
    parser.add_argument("--out", default="", help="结果 JSON 输出路径（默认写到 data/eval/）")
    return parser.parse_args()


def load_dataset(path_value: str, limit: int) -> list[dict]:
    """加载评测集。

    Args:
        path_value: 数据集路径
        limit: 只取前 N 条

    Returns:
        list[dict]: 评测样本
    """
    path = Path(path_value)
    if not path.is_absolute():
        path = (PROJECT_ROOT / path).resolve()
    samples = json.loads(path.read_text(encoding="utf-8"))
    return samples[:limit] if limit else samples


async def run_eval(samples: list[dict], top_k: int) -> dict:
    """执行评测。

    Args:
        samples: 评测样本
        top_k: 检索条数

    Returns:
        dict: 汇总指标与逐条明细
    """
    retriever = await rag.get_retriever()

    hits_at: dict[int, int] = {1: 0, 3: 0, 5: 0}
    reciprocal_ranks: list[float] = []
    recalls: list[float] = []
    latencies: list[float] = []
    status_counter: dict[str, int] = {}
    details: list[dict] = []
    pool_hits = 0
    pool_recalls: list[float] = []
    hit_at_topk = 0

    for index, sample in enumerate(samples, start=1):
        gold = set(sample.get("ground_truth_contexts") or []) | set(sample.get("acceptable_contexts") or [])
        started = time.perf_counter()
        result = await retriever.retrieve(sample["question"], top_k=top_k)
        latency_ms = (time.perf_counter() - started) * 1000

        latencies.append(latency_ms)
        status_counter[result.status] = status_counter.get(result.status, 0) + 1

        retrieved = [chunk.chunk_id for chunk in result.chunks]
        model_inputs = [chunk.chunk_id for chunk in result.candidate_chunks]
        rank = next((position for position, chunk_id in enumerate(retrieved, start=1) if chunk_id in gold), None)
        pool_rank = next(
            (position for position, chunk_id in enumerate(model_inputs, start=1) if chunk_id in gold),
            None,
        )

        for k in hits_at:
            if rank is not None and rank <= k:
                hits_at[k] += 1
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        recalls.append(len(gold & set(retrieved)) / len(gold) if gold else 0.0)
        pool_recalls.append(len(gold & set(model_inputs)) / len(gold) if gold else 0.0)
        if pool_rank is not None:
            pool_hits += 1
        if rank is not None and rank <= top_k:
            hit_at_topk += 1

        details.append(
            {
                "question": sample["question"],
                "gold": sorted(gold),
                "retrieved": retrieved,
                "candidates": model_inputs,
                "first_hit_rank": rank,
                "candidate_rank": pool_rank,
                "status": result.status,
                "top_score": round(result.top_score, 4) if result.top_score is not None else None,
                "latency_ms": round(latency_ms, 1),
            }
        )

        if index % 10 == 0:
            logger.info("eval_progress", done=index, total=len(samples))

    total = len(samples)
    latencies_sorted = sorted(latencies)
    candidate_hit = pool_hits / total
    candidate_recall = statistics.fmean(pool_recalls)
    hit_at_topk_rate = hit_at_topk / total
    summary = {
        "samples": total,
        "top_k": top_k,
        "hit@1": round(hits_at[1] / total, 4),
        "hit@3": round(hits_at[3] / total, 4),
        "hit@5": round(hits_at[5] / total, 4),
        "hit_at_topk": round(hit_at_topk_rate, 4),
        "mrr@5": round(statistics.fmean(reciprocal_ranks), 4),
        "recall@k": round(statistics.fmean(recalls), 4),
        "candidate_hit": round(candidate_hit, 4),
        "candidate_recall": round(candidate_recall, 4),
        "candidate_pool_avg": round(statistics.fmean(len(d["candidates"]) for d in details), 1),
        "ranking_loss_hit": round(candidate_hit - hit_at_topk_rate, 4),
        "ranking_loss_recall": round(candidate_recall - statistics.fmean(recalls), 4),
        "latency_p50_ms": round(latencies_sorted[total // 2], 1),
        "latency_p95_ms": round(latencies_sorted[min(total - 1, int(total * 0.95))], 1),
        "status_distribution": status_counter,
        "config": {
            "embedding_model": settings.EMBEDDING_MODEL_PATH,
            "embedding_dim": settings.EMBEDDING_DIM,
            "retriever_top_k": settings.RETRIEVER_TOP_K,
            "rrf_k": settings.RRF_K,
            "hnsw_ef_search": settings.HNSW_EF_SEARCH,
            "reranker_enabled": settings.RERANKER_ENABLED,
            "reranker_threshold": settings.RERANKER_SCORE_THRESHOLD,
        },
    }
    return {"summary": summary, "details": details}


def print_report(summary: dict) -> None:
    """把汇总指标打成人和 Excel 都能读的表。

    Args:
        summary: 汇总指标
    """
    print("\n" + "=" * 56)
    print(f"检索评测　样本 {summary['samples']} 条　top_k={summary['top_k']}")
    print("=" * 56)
    rows = [
        ("hit@1", summary["hit@1"]),
        ("hit@3", summary["hit@3"]),
        ("hit@5", summary["hit@5"]),
        ("MRR@5", summary["mrr@5"]),
        ("recall@k", summary["recall@k"]),
    ]
    for name, value in rows:
        print(f"{name:<12}{value:>8.2%}")
    print("-" * 56)
    print(f"候选池召回（题）  {summary['candidate_hit']:>6.2%}   池均 {summary['candidate_pool_avg']} 条")
    print(f"候选池召回（条）  {summary['candidate_recall']:>6.2%}")
    print(f"排序损失（题）    {summary['ranking_loss_hit']:>6.2%}   金标进池、但没排进前 {summary['top_k']}")
    print("-" * 56)
    print(f"{'P50 延迟':<12}{summary['latency_p50_ms']:>8.0f} ms")
    print(f"{'P95 延迟':<12}{summary['latency_p95_ms']:>8.0f} ms")
    print(f"{'状态分布':<12}{summary['status_distribution']}")
    print("=" * 56 + "\n")


async def main() -> None:
    """入口。"""
    args = parse_args()
    samples = load_dataset(args.dataset, args.limit)
    try:
        report = await run_eval(samples, args.top_k)
    finally:
        await rag.close_retriever()

    print_report(report["summary"])

    # 文件名带精排开关：做消融对比时两次运行不会互相覆盖
    mode = "rerank" if settings.RERANKER_ENABLED else "rrf"
    out_path = (
        Path(args.out)
        if args.out
        else PROJECT_ROOT / "data" / "eval" / f"result_{mode}_{datetime.now():%Y%m%d_%H%M}.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"明细已写入：{out_path}")


if __name__ == "__main__":
    asyncio.run(main())
