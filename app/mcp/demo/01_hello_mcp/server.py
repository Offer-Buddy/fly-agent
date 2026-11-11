"""① 最小 MCP 服务器 —— 一个文件、两个工具、不依赖任何 Web 框架。

对应课件第二节。这个文件不需要主动运行：
它平时是被客户端当子进程拉起来的（见 ② client.py）。
想单独看它启动，跑 `python -m app.mcp.demo.01_hello_mcp.server`，
会停在 stdio 上等输入，属正常现象。
"""

from datetime import datetime

from fastmcp import FastMCP

mcp = FastMCP("hello")  # 服务器名，客户端靠它区分不同服务器


@mcp.tool  # 这一行把下面的函数登记成一个工具
def add(a: int, b: int) -> int:  # 类型注解 = 参数 schema，不用手写 JSON
    """两个整数相加。

    Args:
        a: 第一个整数。
        b: 第二个整数。
    """
    return a + b  # 函数体就是全部业务，没一行和 MCP 有关


@mcp.tool  # 再加一个工具就再来一遍这两行
def now() -> str:
    """返回当前时间，ISO 格式。"""
    return datetime.now().isoformat(timespec="seconds")


if __name__ == "__main__":
    mcp.run(show_banner=False)  # 默认 stdio：stdin 读 JSON，stdout 写 JSON
