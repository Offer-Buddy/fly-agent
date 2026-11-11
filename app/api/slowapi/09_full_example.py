r"""示例 09 · 完整可运行示例（教程附录 + 第 9 章自定义 429）。

把前面几节拼成一个「像样的」应用：全局兜底额度 + 单路由收紧 + 豁免 +
自定义 429 响应，并且按项目日志规范打一条 ``rate_limit_exceeded``。

和官方处理器 ``_rate_limit_exceeded_handler`` 的区别只有一个：这里换了返回体，
并补上结构化日志，便于在日志里按路径统计「谁被打限了」。
日志三条硬规矩（见 AGENTS.md）：事件名小写加下划线、变量走 kwargs、不用 f-string。

运行：
    uv run python -m app.api.slowapi.09_full_example

验证：
    curl -s http://127.0.0.1:8009/health          # 豁免，始终 200
    for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8009/login; done
    # 第 6 次 429，且终端里会多一条 rate_limit_exceeded 日志
"""

import structlog
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

logger = structlog.get_logger()

# 全局兜底 6/min，单路由可单独收紧
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["6/minute"],
    storage_uri="memory://",
)

app = FastAPI(title="slowapi demo 09 · 完整示例")
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)


@app.exception_handler(RateLimitExceeded)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    """限流超限时的统一响应。

    Args:
        request: 触发限流的请求。
        exc: slowapi 抛出的限流异常，``detail`` 里是命中的规则。

    Returns:
        JSONResponse: 429 响应体。
    """
    logger.warning(
        "rate_limit_exceeded",
        path=request.url.path,
        client_host=request.client.host if request.client else "unknown",
        limit=str(exc.detail),
    )
    return JSONResponse(
        status_code=429,
        content={"detail": "请求太频繁，请稍后再试", "limit": str(exc.detail)},
    )


@app.get("/")
async def root() -> dict[str, str]:
    """受全局限流保护（走 default_limits）。"""
    return {"message": "受全局限流保护"}


@app.get("/login")
@limiter.limit("5/minute")
async def login(request: Request) -> dict[str, str]:
    """登录接口，每分钟最多 5 次。"""
    return {"message": "登录接口，每分钟最多 5 次"}


@app.get("/health")
@limiter.exempt
async def health() -> dict[str, str]:
    """健康检查，豁免限流（否则存活探针会被自己的额度打挂）。"""
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8009)
