"""示例 02 · 全局限流 + 豁免（教程第 4 章）。

要点：
1. ``default_limits`` 只有在挂了 ``SlowAPIMiddleware`` 时才真正生效——
   这是最容易踩的坑：只写 ``default_limits`` 不挂中间件，等于没写；
2. ``@limiter.exempt`` 让指定路由完全跳过限流（健康检查、文档接口）；
3. 挂了中间件之后，``/docs``、``/openapi.json`` 也会被算进全局额度，
   额度设太小会出现「一打开文档页就被 429」。

运行：
    uv run python -m app.api.demo.slowapi.02_global_limits

验证：
    curl -s http://127.0.0.1:8002/          # 前 3 次 200
    curl -s http://127.0.0.1:8002/          # 第 4 次 429
    curl -s http://127.0.0.1:8002/public    # 豁免，始终 200
"""

import uvicorn
from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

# 教程里用的是 1/minute，这里放宽到 3 次，便于用 curl 手动观察
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["3/minute"],
)

app = FastAPI(title="slowapi demo 02 · 全局限流")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]

# 核心步骤：不挂这个中间件，default_limits 不会执行
app.add_middleware(SlowAPIMiddleware)


@app.get("/")
async def home() -> dict[str, str]:
    """未挂装饰器，靠 default_limits 兜底限流。"""
    return {"message": "全局限流生效"}


@app.get("/public")
@limiter.exempt
async def public_endpoint() -> dict[str, str]:
    """豁免路由，不计入任何限额。"""
    return {"status": "ok", "note": "本路由豁免限流"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8002)
