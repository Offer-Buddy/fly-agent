"""把法条知识库暴露成 MCP 服务器 —— 跨进程也能查同一份法条。

**这个文件和 `app/services/rag` 是什么关系**：

    app/services/rag/            真正的检索能力（pgvector + BM25 + 条号 + 精排）
        ├─ app/core/langgraph/tools/legal_search.py   通道一：进程内，给 LangGraph 的 Agent
        └─ app/mcp/legal/server.py                    通道二：跨进程，给任意 MCP 客户端

两条通道共用同一个检索器单例和同一份 JSON 契约（``app.services.rag.payload``），
所以在这里**不该出现任何检索逻辑**：没有 SQL、没有 RRF、没有阈值判断。
工具层只做三件事：收参数 → 调 rag → 序列化。多写一行就是重复实现。

**本文件只做一个工具**：``legal_search``。工具越多，模型选错的概率越大、上下文越长。
其余意图（精确取某一条、查库规模）等这条跑稳了再加。

**为什么值得多开一条通道**：Cursor / WorkBuddy 这类客户端不跑你的 FastAPI，
它们只会说 MCP。把检索包成 MCP，同一份语料就能被这些工具直接查；
另外两个模型的加载（约 50 秒、几百 MB）可以留在独立进程里，不占 Web 进程。

启动方式（stdio，由客户端当子进程拉起）：

    python -m app.mcp.legal.server

    python -m app.mcp.legal.server --transport http --port 8020   # 走 HTTP，便于远程连
"""

from __future__ import annotations

import argparse
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

# 允许 `python app/mcp/legal/server.py` 直接运行：以脚本方式启动时，
# sys.path[0] 是脚本所在目录（app/mcp/legal），`import app.*` 会失败。
# 用 `-m app.mcp.legal.server` 启动时这一步是空操作（根目录已在 sys.path 里）。
PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastmcp import FastMCP

from app.core.logging import logger
from app.services import rag
from app.services.rag.payload import to_payload

# 工具返回给模型的条数上限。不设上限的话，客户端传个 1000 进来，
# 精排要对 1000 条候选逐条打分（CPU 上一条约 150ms），一次调用能把进程钉死。
MAX_TOP_K = 10


def _disabled_payload() -> dict:
    """知识库关闭时的统一返回。

    Returns:
        dict: ``status=disabled``，让客户端知道是「没开」而不是「没查到」
    """
    return {
        "status": "disabled",
        "results": [],
        "hint": "法律知识库未启用（RAG_ENABLED=false），先查配置再重试，不要编造条文。",
    }


def _error_payload(error: Exception) -> dict:
    """检索异常时的统一返回。

    Args:
        error: 捕获到的异常

    Returns:
        dict: ``status=error``，附带一句人话提示
    """
    return {
        "status": "error",
        "results": [],
        "hint": "检索服务异常，先告知用户暂时查不了法条，不要编造条文；可提示稍后重试。",
        "error": str(error),
    }


@asynccontextmanager
async def lifespan(_: FastMCP) -> AsyncIterator[None]:
    """服务器启动时预热、关闭时释放连接池。

    **stdio 模式下没有 FastAPI 的 lifespan**，客户端拉起的是一个裸 Python 进程。
    不预热的话，第一次 ``legal_search`` 要等两个模型加载完（本机 CPU 实测 51 秒），
    而大多数客户端默认超时只有几十秒 —— 表现就是「工具调用超时」，日志里却什么都没错。

    Args:
        _: FastMCP 实例（这里用不到，签名要求）

    Yields:
        None: 把控制权交给服务器主循环
    """
    try:
        await rag.warmup()
        logger.info("mcp_legal_ready")
    except Exception as error:  # noqa: BLE001 - 预热失败仍要起服务，工具层会返回 error
        logger.exception("mcp_legal_warmup_failed", error=str(error))

    yield

    await rag.close_retriever()
    logger.info("mcp_legal_stopped")


mcp = FastMCP("legal-kb", lifespan=lifespan)


@mcp.tool
async def legal_search(query: str, top_k: int = 5) -> dict:
    """本地法条检索：输入一句法律问题或关键词，返回最相关的中国现行法条原文与相关度。

    什么时候用：任何需要条文依据的问题（「这合不合法」「依据哪条」「能赔多少」），
    在给结论之前先调它。不要凭记忆背法条——背错条号比说「查不到」更糟。

    参数：
    - query：核心法律关系词，如「房东扣押金」「违法解除劳动合同」「七天无理由退货」；
      要查特定条文就直接写「民法典第358条」。不要传整段口语，越短越准。
    - top_k：返回条数，默认 5，上限 10。

    返回：
    - status：found 可直接引用；low_quality 最高分低于阈值，须换关键词或说明依据不足；
      no_relevant_result 未命中；unranked 未启用精排。
    - top_rerank_score：最高分，判断可信度用；results[]：law_name、article_no、
      hierarchy、content、rerank_score。
    - hint：非空时按它执行。

    约束：同一 query 不要重复调用；没查到时换更短的关键词或拆成多个小问题，
    而不是换个说法反复试。单次耗时 2~9 秒（CPU 精排），不要在同一个问题里连调三次。
    """
    if not rag.is_enabled():
        return _disabled_payload()

    try:
        retriever = await rag.get_retriever()
        result = await retriever.retrieve(query, top_k=max(1, min(top_k, MAX_TOP_K)))
        return to_payload(result)
    except Exception as error:  # noqa: BLE001 - 工具层兜底：检索失败不该让整轮对话崩掉
        logger.exception("mcp_legal_search_failed", query=query, error=str(error))
        return _error_payload(error)


def main() -> None:
    """命令行入口：默认 stdio，也可起 HTTP 供远程客户端连。"""
    parser = argparse.ArgumentParser(description="法条知识库 MCP 服务器")
    parser.add_argument("--transport", default="stdio", choices=["stdio", "http", "sse", "streamable-http"])
    parser.add_argument("--host", default="127.0.0.1", help="HTTP 模式监听地址")
    parser.add_argument("--port", type=int, default=8020, help="HTTP 模式端口（避开主应用的 8000）")
    args = parser.parse_args()

    if args.transport == "stdio":
        # stdio 不给任何提示，客户端只会看到「连上了但没响应」
        print("legal-kb MCP 服务器已启动（stdio），等待客户端调用…", file=sys.stderr)
        mcp.run(transport="stdio", show_banner=False)
    else:
        mcp.run(transport=args.transport, host=args.host, port=args.port, show_banner=False)


if __name__ == "__main__":
    # psycopg 的异步连接跑不了 Windows 默认的 ProactorEventLoop，
    # 不切 Selector 会卡在「建连接池」上直到超时。app/main.py 和
    # scripts/build_legal_index.py 都做了同一件事，独立进程必须自己来一遍。
    import asyncio

    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    main()
