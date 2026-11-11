r"""示例 03 · 动态限流值（教程 5.1）。

``@limiter.limit`` 的第一个参数既可以是固定字符串，也可以是一个函数。
**但函数签名有坑**：slowapi 调它时传的不是 ``request``，而是 ``key_func`` 的
返回值（形参名必须叫 ``key``）；签名里没有 ``key`` 时它会被**零参数**调用，
写成 ``def dynamic_limit(request: Request)`` 会直接 ``TypeError``。

所以要按角色给不同额度，得让 ``key_func`` 先把角色编进 key 里：

    key_func  →  "admin:127.0.0.1"
    额度函数  →  按 key 前缀返回 "20/minute" 或 "5/minute"

代价：额度字符串每次请求都要重新 ``parse_many``，超高频接口可以自己给 key
前缀加个小字典缓存。

运行：
    uv run python -m app.api.slowapi.03_dynamic_limit

验证（5/min 与 20/min，两个桶互不影响）：
    for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8003/dynamic; done
    for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code}\n" -H "X-Role: admin" http://127.0.0.1:8003/dynamic; done
    前者第 6 次 429，后者 6 次全 200
"""

import uvicorn
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="slowapi demo 03 · 动态额度")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]


def key_with_role(request: Request) -> str:
    """把角色编进限流键，形如 ``"admin:1.2.3.4"``。

    demo 里角色取自请求头（客户端可伪造），真实项目要从 token / 会话里取。

    Args:
        request: 当前请求。

    Returns:
        str: ``"<role>:<ip>"`` 形式的限流键。
    """
    role = request.headers.get("X-Role", "user")
    return f"{role}:{get_remote_address(request)}"


def limit_by_role(key: str) -> str:
    """按限流键的前缀返回额度字符串。

    Args:
        key: ``key_func`` 的返回值，此处形如 ``"admin:1.2.3.4"``。

    Returns:
        str: slowapi 限额字符串，如 ``"20/minute"``。
    """
    role = key.split(":", 1)[0]
    return "20/minute" if role == "admin" else "5/minute"


@app.get("/dynamic")
@limiter.limit(limit_by_role, key_func=key_with_role)
async def dynamic_route(request: Request) -> dict[str, str]:
    """同一路由，两种角色走两套额度。"""
    key = key_with_role(request)
    return {"key": key, "limit": limit_by_role(key), "message": "动态限流"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8003)
