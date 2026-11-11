"""⑦ 外壳：REST 路由 + 挂载 MCP + 装网关。

对应课件第四节。集成本身的全部工作量，就是下面标了 ①②③④ 的四行。
"""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from . import service
from .config import USERS
from .security import Gateway, issue_token
from .tools import mcp

mcp_app = mcp.http_app(path="/")  # ① MCP 变成 ASGI 子应用


@asynccontextmanager
async def lifespan(app: FastAPI):
    """合并两边的生命周期。

    你自己原来的预热、收尾写在 `async with` 外面，一行都不用改。

    Args:
        app: FastAPI 实例，MCP 子应用需要它来对齐启动/关闭时机。

    Yields:
        控制权交回 FastAPI，直到进程退出。
    """
    # 你原来的预热、收尾写在这里，一行都不用改
    async with mcp_app.lifespan(app):  # ② 合并生命周期，不是替换
        yield


app = FastAPI(title="tasks", lifespan=lifespan)
app.add_middleware(Gateway)  # ③ 网关在最外层，两条通道都罩住
app.mount("/mcp", mcp_app)  # ④ 端点 = /mcp/（末尾有斜杠）


class LoginIn(BaseModel):
    """登录入参。"""

    username: str
    password: str


class TaskIn(BaseModel):
    """新建任务入参。"""

    title: str


@app.get("/health")
async def health() -> dict:
    """探活，白名单路径，不用带票。

    Returns:
        固定返回 `{"status": "ok"}`。
    """
    return {"status": "ok"}


@app.post("/api/token")
async def login(payload: LoginIn) -> dict:
    """演示用的登录：真实项目查库校验密码。

    Args:
        payload: 用户名与密码。

    Returns:
        成功给 `access_token`，失败给 `{"error": "bad_credentials"}`。
    """
    if USERS.get(payload.username) != payload.password:
        return {"error": "bad_credentials"}
    return {"access_token": issue_token(payload.username), "token_type": "bearer"}


@app.get("/api/tasks")
async def list_tasks(request: Request) -> dict:
    """REST 通道：和 MCP 的 list_tasks 落到同一个 service 函数。

    Args:
        request: 由 FastAPI 注入，`state.username` 是网关写进来的。

    Returns:
        当前用户和他的任务列表。
    """
    user = request.state.username  # 网关放行时已经解好，不用再验
    return {"user": user, "items": await service.list_tasks(user)}


@app.post("/api/tasks")
async def create_task(payload: TaskIn, request: Request) -> dict:
    """REST 通道：新建任务。

    Args:
        payload: 任务标题。
        request: 由 FastAPI 注入，取 `state.username` 当归属人。

    Returns:
        刚建好的任务记录。
    """
    return await service.create_task(payload.title, request.state.username)


@app.post("/api/tasks/{task_id}/done")
async def complete_task(task_id: int, request: Request) -> dict:
    """REST 通道：标记任务完成。

    Args:
        task_id: 任务 id。
        request: 由 FastAPI 注入，取 `state.username` 校验归属。

    Returns:
        更新后的任务记录。

    Raises:
        HTTPException: 任务不存在或不属于当前用户，一律 404。
    """
    try:
        return await service.complete_task(task_id, request.state.username)
    except LookupError as exc:
        # 别人的任务和不存在，对外表现一致，不泄露"这个 id 存在"
        raise HTTPException(status_code=404, detail=str(exc)) from exc


if __name__ == "__main__":
    # 直接 python -m app.mcp.demo.02_task_app.main 起服务，端口避开 8000
    uvicorn.run(app, host="127.0.0.1", port=8010)
