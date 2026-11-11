"""② 以客户端身份连上 ① 那个服务器，看它到底暴露了什么。

对应课件第二节。跑法：`python -m app.mcp.demo.01_hello_mcp.client`
server.py 不用自己起 —— 这里会把它当子进程拉起来。
"""

import asyncio
from pathlib import Path

from fastmcp import Client

# 同目录那个服务器，给绝对路径最稳（客户端要靠它启动子进程）
SERVER = Path(__file__).with_name("server.py")


async def main() -> None:
    """拉起服务器、列工具、调两次工具。"""
    async with Client(SERVER) as client:  # 传 Path = 用 stdio 拉起子进程
        print("工具清单：", [t.name for t in await client.list_tools()])
        print("1 + 1   ：", (await client.call_tool("add", {"a": 1, "b": 1})).data)
        print("现在几点：", (await client.call_tool("now", {})).data)


if __name__ == "__main__":
    asyncio.run(main())
