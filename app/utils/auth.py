"""JWT 令牌的签发与校验。

注意：``ValueError`` 的文案最终会作为 422 响应的 detail 返回给客户端，
属于对外契约，保持英文。
"""

import re
from datetime import (
    UTC,
    datetime,
    timedelta,
)
from typing import Optional

from jose import (
    JWTError,
    jwt,
)

from app.core.config import settings
from app.core.logging import logger
from app.schemas.auth import Token
from app.utils.sanitization import sanitize_string


def create_access_token(thread_id: str, expires_delta: Optional[timedelta] = None) -> Token:
    """为某个 thread 签发访问令牌。

    Args:
        thread_id: 会话（thread）唯一标识，写进 ``sub`` 声明
        expires_delta: 自定义有效期；不传则用配置里的天数

    Returns:
        Token: 生成的访问令牌
    """
    if expires_delta:
        expire = datetime.now(UTC) + expires_delta
    else:
        expire = datetime.now(UTC) + timedelta(days=settings.JWT_ACCESS_TOKEN_EXPIRE_DAYS)

    to_encode = {
        "sub": thread_id,
        "exp": expire,
        "iat": datetime.now(UTC),
        # jti 保证同一 thread 多次签发的令牌互不相同
        "jti": sanitize_string(f"{thread_id}-{datetime.now(UTC).timestamp()}"),
    }

    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)

    logger.info("token_created", thread_id=thread_id, expires_at=expire.isoformat())

    return Token(access_token=encoded_jwt, expires_at=expire)


def verify_token(token: str) -> Optional[str]:
    """校验 JWT 并返回其中的 thread ID。

    Args:
        token: 待校验的 JWT

    Returns:
        Optional[str]: 校验通过返回 thread ID，令牌无效返回 None

    Raises:
        ValueError: 令牌格式非法（不是三段式 base64url）
    """
    if not token or not isinstance(token, str):
        logger.warning("token_invalid_format")
        raise ValueError("Token must be a non-empty string")

    # 解码前先做基本格式校验：
    # JWT 由三段 base64url 组成，用点分隔
    if not re.match(r"^[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+\.[A-Za-z0-9-_]+$", token):
        logger.warning("token_suspicious_format")
        raise ValueError("Token format is invalid - expected JWT format")

    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
        thread_id: str | None = payload.get("sub")
        if thread_id is None:
            logger.warning("token_missing_thread_id")
            return None

        logger.info("token_verified", thread_id=thread_id)
        return thread_id

    except JWTError as e:
        # 过期、签名不符等都归到这里，返回 None 由调用方决定回什么状态码
        logger.error("token_verification_failed", error=str(e))
        return None
