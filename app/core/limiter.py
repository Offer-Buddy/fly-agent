"""应用限流配置。

基于 slowapi 实现，默认额度取自配置项 ``RATE_LIMIT_DEFAULT``，
按客户端 IP 计数。

配置了 Redis 时，用它做分布式存储后端，
这样多副本部署时各实例共享同一份额度，不会出现「一个 IP 打十个实例」
绕过限制的情况。
"""

from urllib.parse import quote

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.cache import REDIS_AVAILABLE
from app.core.config import settings
from app.core.logging import logger

# 按配置拼出 Redis 连接串。redis 本身是核心依赖，
# 但仍保留可用性判断：裁剪过的环境退化为内存存储，
# 而不是在导入阶段抛 ConfigurationError。
_storage_uri = None
if settings.REDIS_HOST and REDIS_AVAILABLE:
    _password_part = f":{quote(settings.REDIS_PASSWORD, safe='')}@" if settings.REDIS_PASSWORD else ""
    _storage_uri = f"redis://{_password_part}{settings.REDIS_HOST}:{settings.REDIS_PORT}/{settings.REDIS_DB}"
    logger.info("rate_limiter_using_redis", host=settings.REDIS_HOST, port=settings.REDIS_PORT)
elif settings.REDIS_HOST:
    logger.warning(
        "rate_limiter_redis_configured_but_client_missing",
        hint="redis ships as a core dependency — reinstall with: uv sync",
    )

# 初始化限流器：没有 Redis 时自动使用内存存储
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=settings.RATE_LIMIT_DEFAULT,  # pyright: ignore[reportArgumentType]
    storage_uri=_storage_uri,
)
