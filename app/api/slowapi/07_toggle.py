r"""示例 07 · 运行时开关限流（教程 5.5）。

``limiter.enabled`` 是一个普通属性，可以随时切换：

- 压测 / 调试时关掉，避免被自己的额度拦住；
- 出故障时先关限流保可用，再定位问题。

注意 ``/toggle`` 本身**不能挂限流装饰器**——一旦它被限流拦住，
限流关掉之后就再也调不动接口了。

运行：
    uv run python -m app.api.slowapi.07_toggle

验证：
    curl "http://127.0.0.1:8007/state"            # {"enabled": true}
    for i in $(seq 1 4); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8007/ping; done
    curl "http://127.0.0.1:8007/toggle?enabled=false"
    for i in $(seq 1 4); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8007/ping; done   # 全 200
"""

import uvicorn
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# 初始启用；测试环境也可以直接建 Limiter(..., enabled=False)
limiter = Limiter(key_func=get_remote_address, enabled=True)

app = FastAPI(title="slowapi demo 07 · 动态开关")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]


@app.get("/state")
async def state() -> dict[str, bool]:
    """回显当前限流开关状态（自身不限流）。"""
    return {"enabled": limiter.enabled}


@app.get("/toggle")
async def toggle(enabled: bool = True) -> dict[str, bool]:
    """切换限流开关（自身不限流，否则关掉之后调不回来）。"""
    limiter.enabled = enabled
    return {"enabled": limiter.enabled}


@app.get("/ping")
@limiter.limit("2/minute")
async def ping(request: Request) -> dict[str, str]:
    """受控接口：额度 2/minute，关掉限流后不再计数。"""
    return {"message": "pong"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8007)
