"""建法律知识库索引：分块 → 向量化 → 写入 pgvector。

用法：

    python scripts/build_legal_index.py                 # 用配置里的语料目录
    python scripts/build_legal_index.py --corpus data/laws
    python scripts/build_legal_index.py --clear         # 先清空再重建
    python scripts/build_legal_index.py --limit 3       # 只建前 3 部，快速验证链路

设计要点：

- **按法规逐部写入**，不是全部读进内存再一把写。1260 条 × 512 维 ≈ 2.5MB，
  一部一部来，中途失败只丢一部，不用从头再跑
- **幂等**：``replace_law_chunks`` 先删该法规的旧分块再插，
  重复跑不会出现重复数据，改完分块规则直接重跑即可
- **写库前校验维度**：维度不对时 pgvector 报的错很难读，这里提前给出人话提示
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

# Windows 上 psycopg 的异步连接池不支持默认的 ProactorEventLoop，
# 不切 SelectorEventLoop 会一直报「couldn't get a connection」。
# app/main.py 在服务启动时也做了同样的切换，这里独立跑脚本必须自己来一遍。
if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 允许直接 `python scripts/build_legal_index.py` 运行（把项目根目录加进 sys.path）
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.core.config import settings  # noqa: E402
from app.core.logging import logger  # noqa: E402
from app.services.rag import embedder  # noqa: E402
from app.services.rag.chunker import (  # noqa: E402
    chunk_law_file,
    parse_law_meta,
)
from app.services.rag.keyword import tokenize  # noqa: E402
from app.services.rag.pg_store import law_store  # noqa: E402


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    Returns:
        argparse.Namespace: 参数对象
    """
    parser = argparse.ArgumentParser(description="构建法律知识库索引")
    parser.add_argument(
        "--corpus",
        default=settings.LAW_CORPUS_DIR,
        help=f"语料目录（默认 {settings.LAW_CORPUS_DIR}）",
    )
    parser.add_argument("--clear", action="store_true", help="先清空 law / law_chunk 再建")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 部法规（0 表示全部）")
    parser.add_argument("--batch-size", type=int, default=settings.EMBEDDING_BATCH_SIZE, help="向量化批大小")
    return parser.parse_args()


def resolve_corpus(path_value: str) -> Path:
    """把语料路径解析成绝对路径。

    Args:
        path_value: 命令行传入的路径

    Returns:
        Path: 绝对路径
    """
    path = Path(path_value)
    return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()


def embedding_text(chunk) -> str:
    """拼出「送去编码」的文本：法名 + 章节 + 条文。

    为什么不直接编码 ``chunk.content``：条文正文经常没有主语。
    「租赁期限届满，承租人应当返还租赁物」——单看这句，模型不知道它属于
    租赁合同还是仓储合同；补上「民法典｜第十四章 租赁合同」，语义落点更清楚。

    注意：实测（100 条评测集）这一步对 hit@5 没有明显提升，主要收益在于
    跨法同名条文的区分（多个法都有「第一条」）。真正的瓶颈是召回深度与
    向量模型本身，见 ``scripts/diagnose_retrieval.py`` 的分阶段数字。

    ``content`` 本身不改：它是给人看的、也是 BM25 与精排读的那一份，
    加前缀会污染关键词统计。

    Args:
        chunk: 分块

    Returns:
        str: 用于编码的文本
    """
    parts = [chunk.law_name]
    if chunk.hierarchy:
        # 只取最后两级：「第三编 合同 > 第十四章 租赁合同」→「第十四章 租赁合同」
        levels = [level.strip() for level in chunk.hierarchy.split(">") if level.strip()]
        parts.extend(levels[-1:])
    parts.append(chunk.content)
    return "｜".join(parts)


async def build(corpus_dir: Path, clear: bool, limit: int, batch_size: int) -> None:
    """执行建库。

    Args:
        corpus_dir: 语料目录
        clear: 是否先清空
        limit: 只处理前 N 部
        batch_size: 向量化批大小
    """
    if not corpus_dir.exists():
        raise FileNotFoundError(f"语料目录不存在：{corpus_dir}")

    files = sorted(corpus_dir.glob("*.txt"))
    if limit:
        files = files[:limit]
    if not files:
        raise RuntimeError(f"{corpus_dir} 下没有 txt 语料")

    logger.info("legal_index_start", corpus=str(corpus_dir), files=len(files), clear=clear)

    if not embedder.model_available():
        raise RuntimeError(
            f"向量模型不存在：{embedder._resolve_model_path()}。"
            "把 bge-small-zh-v1.5 放到该目录，或改 EMBEDDING_MODEL_PATH。"
        )

    if clear:
        await law_store.clear()

    started = time.perf_counter()
    total_chunks = 0
    total_laws = 0

    for index, file_path in enumerate(files, start=1):
        law_started = time.perf_counter()
        meta = parse_law_meta(file_path)
        chunks = chunk_law_file(file_path)
        if not chunks:
            logger.warning("legal_index_empty_file", file=file_path.name)
            continue

        law_id = await law_store.upsert_law(meta)

        embeddings: list[list[float]] = []
        # 分批编码，避免一次性把 1260 条 × 512 维塞进内存
        for offset in range(0, len(chunks), batch_size):
            batch = chunks[offset : offset + batch_size]
            embeddings.extend(
                embedder.embed_documents_sync([embedding_text(chunk) for chunk in batch], batch_size)
            )

        first_dimension = len(embeddings[0]) if embeddings else 0
        if first_dimension != settings.EMBEDDING_DIM:
            raise RuntimeError(
                f"向量维度不匹配：模型输出 {first_dimension} 维，配置 EMBEDDING_DIM={settings.EMBEDDING_DIM}。"
                "改配置没用的话要改表结构（vector(512) 是定长类型）。"
            )

        written = await law_store.replace_law_chunks(
            law_id=law_id,
            chunks=chunks,
            embeddings=embeddings,
            status=meta.status,
            effective_date=meta.effective_date,
            token_counts=[len(tokenize(chunk.content)) for chunk in chunks],
        )
        total_chunks += written
        total_laws += 1

        logger.info(
            "legal_index_law_done",
            progress=f"{index}/{len(files)}",
            law=meta.short_name,
            chunks=written,
            status=meta.status,
            seconds=round(time.perf_counter() - law_started, 2),
        )

    # 批量写入后必须 ANALYZE：统计信息不新鲜时，规划器会误判表的大小，
    # 该走 HNSW 的向量查询会退化成全表扫描（实测 2.1ms → 51ms，慢 24 倍）
    await law_store.analyze()

    elapsed = round(time.perf_counter() - started, 1)
    stored = await law_store.count_chunks()
    active = await law_store.count_chunks(only_active=True)
    logger.info(
        "legal_index_finished",
        laws=total_laws,
        written=total_chunks,
        stored=stored,
        active=active,
        seconds=elapsed,
    )
    print(
        f"\n建库完成：{total_laws} 部法规 / 写入 {total_chunks} 条 / 库内共 {stored} 条（现行 {active} 条）"
        f" / 耗时 {elapsed} 秒"
    )


async def main() -> None:
    """入口：解析参数、执行建库、关闭连接池。"""
    args = parse_args()
    try:
        await build(resolve_corpus(args.corpus), args.clear, args.limit, args.batch_size)
    finally:
        await law_store.close()


if __name__ == "__main__":
    asyncio.run(main())
