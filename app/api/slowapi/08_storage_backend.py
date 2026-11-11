r"""示例 08 · 存储后端：内存 / Redis / 限流策略（教程第 6 章）。

三种角色，一个文件讲完：

1. **内存存储**：不配 ``REDIS_HOST`` 就是内存，零依赖，但多进程各算各的；
2. **Redis 存储**：填了 ``REDIS_HOST`` 就统一计数，多副本部署必须走这条；
3. **strategy**：``fixed-window`` / ``sliding-window`` 等计数策略，用环境变量切。

沿用主项目**同名环境变量**（``REDIS_HOST`` / ``REDIS_PORT`` / ``REDIS_DB`` /
``REDIS_PASSWORD``），但不 import ``app.core.settings``——这样单文件跑不需要
把 JWT、数据库那些必填项配齐。密码里有 ``@ : /`` 必须 ``quote()``，
否则连接串会被拼错（主项目 ``app/services/memory.py`` 就栽在这上面）。

运行（内存模式）：
    uv run python -m app.api.slowapi.08_storage_backend
    uv run python -m app.api.slowapi.08_storage_backend   # 看 /info 里的 backend

运行（Redis 模式）：
    REDIS_HOST=127.0.0.1 REDIS_PORT=6379 REDIS_DB=1 \
    LIMITER_STRATEGY=sliding-window \
    uv run python -m app.api.slowapi.08_storage_backend
"""

import os
from urllib.parse import quote

import redis
import structlog
import uvicorn
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

logger = structlog.get_logger()

REDIS_HOST = os.getenv("REDIS_HOST", "")
REDIS_PORT = os.getenv("REDIS_PORT", "6379")
REDIS_DB = os.getenv("REDIS_DB", "1")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
STRATEGY = os.getenv("LIMITER_STRATEGY", "fixed-window")

# 额度放在这里而不是路由上，便于对照「多进程时阈值被放大 N 倍」的现象
LIMIT_RULE = "5/minute"


def _build_storage_uri() -> str | None:
    """按环境变量拼 Redis 连接串；没有 host 时返回 ``None``（即内存存储）。

    Returns:
        str | None: 形如 ``redis://:pass@host:6379/1`` 的连接串，或 ``None``。
    """
    if not REDIS_HOST:
        return None
    password_part = f":{quote(REDIS_PASSWORD, safe='')}@" if REDIS_PASSWORD else ""
    return f"redis://{password_part}{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"


def _probe(uri: str) -> bool:
    """探测 Redis 连通性，连不上就退回内存，避免每条请求都 500。

    Args:
        uri: Redis 连接串。

    Returns:
        bool: 可用返回 ``True``。
    """
    client = redis.Redis(
        host=REDIS_HOST,
        port=int(REDIS_PORT),
        db=int(REDIS_DB),
        password=REDIS_PASSWORD or None,
        socket_connect_timeout=2,
    )
    try:
        client.ping()
    except Exception:
        logger.warning("rate_limiter_redis_unreachable", host=REDIS_HOST, port=REDIS_PORT)
        return False
    return True


_storage_uri = _build_storage_uri()
if _storage_uri and not _probe(_storage_uri):
    _storage_uri = None

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[LIMIT_RULE],
    storage_uri=_storage_uri,
    strategy=STRATEGY,
)

app = FastAPI(title="slowapi demo 08 · 存储后端")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]

logger.info(
    "rate_limiter_ready",
    backend="redis" if _storage_uri else "memory",
    strategy=STRATEGY,
    limit=LIMIT_RULE,
)


def _masked_uri() -> str:
    """把连接串里的密码遮掉再返回。

    Returns:
        str: 对外的展示用连接串。
    """
    if not _storage_uri:
        return "memory://"
    if REDIS_PASSWORD:
        return _storage_uri.replace(quote(REDIS_PASSWORD, safe=""), "***")
    return _storage_uri


@app.get("/info")
@limiter.exempt
async def info() -> dict[str, str]:
    """回显当前存储后端与策略（豁免限流，方便反复查）。"""
    return {
        "backend": "redis" if _storage_uri else "memory",
        "storage_uri": _masked_uri(),
        "strategy": STRATEGY,
        "limit": LIMIT_RULE,
    }


@app.get("/hit")
async def hit(request: Request) -> dict[str, str]:
    """受控接口：额度由 default_limits 兜底。"""
    return {"message": "hit", "backend": "redis" if _storage_uri else "memory"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8008)
