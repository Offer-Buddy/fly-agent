"""⑥ MCP 工具层：把 service 包成三个工具，别的什么都不做。

对应课件第四节、第五节。工具自己不记身份，也不做数据过滤 ——
身份从请求头还原，过滤交给 ④ service。所以这里的函数都很薄。
"""

from typing import Any

from fastmcp import FastMCP
from fastmcp.server.dependencies import get_http_headers

from . import service
from .security import user_of

mcp = FastMCP("task-tools")  # 名字只作标识，不参与模型决策


def caller() -> str:
    """从请求头还原调用者。

    `get_http_headers()` 默认会剔掉 authorization 和 cookie，
    不写 include 就永远读到空字符串 —— 这条最坑。

    Returns:
        当前调用者的用户名。

    Raises:
        ValueError: 请求头里没有可用的 Bearer token。
    """
    auth = get_http_headers(include={"authorization"}).get("authorization", "")  # 不写 include 拿不到票
    username = user_of(auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if username is None:  # 没票、票坏了或过期
        raise ValueError("无法识别调用者：缺少 Bearer token")
    return username


@mcp.tool  # 登记成工具，下面的函数就是实现
async def whoami() -> dict[str, Any]:
    """返回当前调用者，用来确认鉴权链路通了。

    Returns:
        形如 `{"username": "alice"}` 的字典。
    """
    return {"username": caller()}


@mcp.tool
async def create_task(title: str) -> dict[str, Any]:
    """新建一个任务。

    Args:
        title: 任务标题。

    Returns:
        刚建好的任务记录。
    """
    return await service.create_task(title, caller())  # 认人 + 转业务，两件事都在这行


@mcp.tool
async def list_tasks() -> list[dict[str, Any]]:
    """列出当前用户的任务。

    Returns:
        该用户的全部任务；没有就是空列表。
    """
    return await service.list_tasks(caller())
