"""验收探针：以客户端身份连上法条 MCP 服务器，把「通没通」变成几行能看的输出。

不参与业务，也不被任何代码 import。作用只有一个：
把「工具清单对不对、检索返回什么、低分时 hint 有没有」一次性打出来，省得在客户端里瞎点。

跑法（stdio，服务器由本脚本当子进程拉起，不用自己起）：

    python -m app.mcp.legal.client

跑法（HTTP，先起服务器）：

    python -m app.mcp.legal.server --transport http --port 8020
    python -m app.mcp.legal.client --http
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import (
    StdioTransport,
    StreamableHttpTransport,
)

# 项目根：stdio 子进程要用 `-m app.mcp.legal.server` 启动，
# 而 `-m` 只在 cwd（或 PYTHONPATH）里能找到 `app` 包时生效。
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def stdio_transport() -> StdioTransport:
    """构造 stdio 传输：用当前解释器 + 模块名启动服务器。

    Returns:
        StdioTransport: 指向法条服务器的 stdio 传输
    """
    return StdioTransport(
        command=sys.executable,
        args=["-m", "app.mcp.legal.server"],
        cwd=str(PROJECT_ROOT),
        # 子进程的 stderr 落到日志文件，排查「连上了但没响应」时能看见服务端说了什么
        log_file=PROJECT_ROOT / "logs" / "mcp_legal_server.log",
    )


def show(title: str, payload: object) -> None:
    """按统一格式打印一段结果。

    Args:
        title: 标题
        payload: 任意可 JSON 序列化的内容
    """
    print(f"\n── {title} " + "─" * max(0, 52 - len(title)))
    print(json.dumps(payload, ensure_ascii=False, indent=2)[:1200])


async def run(client: Client) -> None:
    """跑完整套验收：工具清单 → 命中 → 条号直查 → 低质量分支。

    Args:
        client: 已连上的 MCP 客户端
    """
    tools = await client.list_tools()
    print("工具清单：" + ", ".join(tool.name for tool in tools))

    started = time.perf_counter()
    result = (await client.call_tool("legal_search", {"query": "试用期最长多久", "top_k": 3})).data
    show("① 命中（legal_search）", result)
    print(f"   耗时 {time.perf_counter() - started:.2f}s")

    started = time.perf_counter()
    result = (await client.call_tool("legal_search", {"query": "民法典第358条", "top_k": 3})).data
    print(f"\n② 条号直查 → status={result['status']} top={result['top_rerank_score']} "
          f"{time.perf_counter() - started:.2f}s")
    print("   第一条：" + result["results"][0]["law_name"] + result["results"][0]["article_no"])

    result = (await client.call_tool("legal_search", {"query": "公司年会抽奖算赌博吗", "top_k": 2})).data
    print(f"\n③ 低质量问句 → status={result['status']} top={result['top_rerank_score']}")
    print("   hint：" + result["hint"])


async def main() -> None:
    """按参数选择 stdio 或 HTTP 两种连法。"""
    parser = argparse.ArgumentParser(description="法条 MCP 服务器验收探针")
    parser.add_argument("--http", action="store_true", help="连 HTTP 模式（默认 stdio）")
    parser.add_argument("--base", default="http://127.0.0.1:8020", help="HTTP 模式的服务地址")
    args = parser.parse_args()

    if args.http:
        async with Client(StreamableHttpTransport(f"{args.base}/mcp/"), timeout=90) as client:
            await run(client)
    else:
        # 传 StdioTransport = 让本脚本充当客户端，把服务器当子进程拉起
        async with Client(stdio_transport(), timeout=90) as client:  # 预热 51 秒，别用默认值
            await run(client)


if __name__ == "__main__":
    asyncio.run(main())
