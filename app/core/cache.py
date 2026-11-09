"""缓存服务，可选 Redis 后端。

配置了 ``REDIS_HOST`` 就用 Redis 做分布式缓存；
没配置则退化为进程内的 TTL 缓存（多实例之间不共享）。
"""

import hashlib
import time
from typing import (
    TYPE_CHECKING,
    Awaitable,
    Optional,
    cast,
)

from app.core.config import settings
from app.core.logging import logger

# redis 已是核心依赖；保留这层探测是为了让裁剪过的部署环境
# 退化为内存缓存，而不是在导入阶段直接崩掉。
if TYPE_CHECKING:
    from redis.asyncio import Redis  # pyright: ignore[reportMissingImports]

    REDIS_AVAILABLE = True
else:
    try:
        from redis.asyncio import Redis

        REDIS_AVAILABLE = True
    except ImportError:
        logger.debug("redis_client_not_available")
        Redis = None
        REDIS_AVAILABLE = False


class InMemoryCacheService:
    """Redis 不可用时的进程内 TTL 缓存。"""

    def __init__(self, default_ttl: int = 60):
        """初始化内存缓存。

        Args:
            default_ttl: 缓存条目的默认存活时间（秒）
        """
        self._cache: dict[str, tuple[float, str]] = {}
        self._default_ttl = default_ttl

    async def initialize(self) -> None:
        """内存缓存无需建立连接，这里只打一条日志。"""
        logger.info("cache_initialized", backend="in_memory", ttl=self._default_ttl)

    async def get(self, key: str) -> Optional[str]:
        """读取缓存。

        Args:
            key: 缓存键

        Returns:
            缓存值；不存在或已过期时返回 None
        """
        entry = self._cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() > expires_at:
            del self._cache[key]
            return None
        return value

    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        """写入缓存并设置过期时间。

        Args:
            key: 缓存键
            value: 要缓存的值
            ttl: 存活时间（秒），不传则用默认值
        """
        expires_at = time.monotonic() + (ttl or self._default_ttl)
        self._cache[key] = (expires_at, value)

    async def delete(self, key: str) -> None:
        """删除一个缓存条目。

        Args:
            key: 缓存键
        """
        self._cache.pop(key, None)

    async def close(self) -> None:
        """清空内存缓存。"""
        self._cache.clear()


class RedisCacheService:
    """基于 Redis 的分布式缓存后端。"""

    def __init__(self, default_ttl: int = 60):
        """初始化 Redis 缓存服务（此时尚未建立连接）。

        Args:
            default_ttl: 缓存条目的默认存活时间（秒）
        """
        self._client: Optional[Redis] = None
        self._default_ttl = default_ttl

    async def initialize(self) -> None:
        """连接 Redis 服务端。"""
        client = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD or None,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=True,
        )
        await cast(Awaitable[bool], client.ping())
        self._client = client
        logger.info(
            "cache_initialized",
            backend="redis",
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            ttl=self._default_ttl,
        )

    async def get(self, key: str) -> Optional[str]:
        """从 Redis 读取缓存。

        Args:
            key: 缓存键

        Returns:
            缓存值；不存在时返回 None
        """
        if not self._client:
            return None
        try:
            return await self._client.get(key)
        except Exception as e:
            # 缓存故障不影响主流程，降级为「未命中」
            logger.warning("cache_get_failed", key=key, error=str(e))
            return None

    async def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        """写入 Redis 并设置过期时间。

        Args:
            key: 缓存键
            value: 要缓存的值
            ttl: 存活时间（秒），不传则用默认值
        """
        if not self._client:
            return
        try:
            await self._client.set(key, value, ex=(ttl or self._default_ttl))
        except Exception as e:
            logger.warning("cache_set_failed", key=key, error=str(e))

    async def delete(self, key: str) -> None:
        """删除 Redis 中的一个键。

        Args:
            key: 缓存键
        """
        if not self._client:
            return
        try:
            await self._client.delete(key)
        except Exception as e:
            logger.warning("cache_delete_failed", key=key, error=str(e))

    async def close(self) -> None:
        """关闭 Redis 连接。"""
        if self._client:
            await self._client.aclose()
            logger.info("cache_connection_closed")


def _create_cache_service() -> InMemoryCacheService | RedisCacheService:
    """按配置创建对应的缓存实现。

    Returns:
        缓存服务实例：配置了 Redis 且客户端可用时返回 Redis 实现，否则返回内存实现
    """
    ttl = settings.CACHE_TTL_SECONDS

    if settings.REDIS_HOST and REDIS_AVAILABLE:
        return RedisCacheService(default_ttl=ttl)

    if settings.REDIS_HOST and not REDIS_AVAILABLE:
        logger.warning(
            "redis_client_not_installed",
            hint="redis ships as a core dependency — reinstall with: uv sync",
        )

    return InMemoryCacheService(default_ttl=ttl)


def cache_key(prefix: str, *parts: str) -> str:
    """拼装缓存键：前缀 + 各组成部分的哈希。

    拼出来的键长度固定，避免把长文本（如整段用户输入）直接当键。

    Args:
        prefix: 键前缀，例如 "memory"
        *parts: 参与计算哈希的组成部分

    Returns:
        确定性的缓存键字符串
    """
    raw = ":".join(parts)
    hashed = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"{prefix}:{hashed}"


# 全局缓存服务单例——在应用 lifespan 中懒初始化
cache_service = _create_cache_service()
