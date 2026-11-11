"""示例 05 · 自定义限流键（教程 5.3）。

默认按 IP 计数，但真实业务往往要按「用户 + 套餐」分桶：

- 全局键函数：``Limiter(key_func=key_by_ip_and_plan)``；
- 单路由覆盖：``@limiter.limit(..., key_func=key_by_user)``。

两个必须注意的点：
1. **未登录时一定要回退到 IP**。如果统一返回 ``"anonymous"``，所有匿名请求会
   共用同一个计数桶，一个人就能把额度打满，等于给全站上了个单点。
2. 生产环境取用户身份要从 token / 会话里取，不能读 query 参数（客户端可伪造）。

运行：
    uv run python -m app.api.demo.slowapi.05_custom_key

验证（同一 IP、不同套餐各自独立计数）：
    curl -H "X-Plan: free" http://127.0.0.1:8005/by-plan
    curl -H "X-Plan: pro"  http://127.0.0.1:8005/by-plan
    # 按用户 ID 分桶：user_id 不同则互不影响
    curl "http://127.0.0.1:8005/by-user?user_id=alice"
    curl "http://127.0.0.1:8005/by-user?user_id=bob"
"""

import uvicorn
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="slowapi demo 05 · 自定义键")


def key_by_ip_and_plan(request: Request) -> str:
    """按「IP + 套餐」组合分桶。

    Args:
        request: 当前请求，套餐取自 ``X-Plan`` 请求头。

    Returns:
        str: 形如 ``"1.2.3.4:pro"`` 的限流键。
    """
    ip = get_remote_address(request)
    plan = request.headers.get("X-Plan", "free")
    return f"{ip}:{plan}"


def key_by_user(request: Request) -> str:
    """按用户 ID 分桶，未登录回退到 IP。

    Args:
        request: 当前请求，用户标识取自 ``user_id`` 查询参数（demo 用）。

    Returns:
        str: 用户标识或客户端 IP。
    """
    user_id = request.query_params.get("user_id")
    # 关键：不要返回固定的 "anonymous"，否则匿名流量共用一个桶
    return user_id or get_remote_address(request)


# 换成组合键的限流器：/by-plan 走这个全局键
plan_limiter = Limiter(key_func=key_by_ip_and_plan, default_limits=[])
app.state.limiter = plan_limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]


@app.get("/by-plan")
@plan_limiter.limit("5/minute")
async def by_plan(request: Request) -> dict[str, str]:
    """同一 IP、不同套餐互不占用额度。"""
    return {"key": key_by_ip_and_plan(request), "message": "按 IP + 套餐限流"}


@app.get("/by-user")
@limiter.limit("3/minute", key_func=key_by_user)
async def by_user(request: Request) -> dict[str, str]:
    """单路由覆盖全局键函数，按用户 ID 限流。"""
    return {"key": key_by_user(request), "message": "按用户 ID 限流"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8005)
