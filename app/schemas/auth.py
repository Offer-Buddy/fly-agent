"""鉴权相关请求 / 响应模型。

``description`` 会显示在 Swagger 文档里，因此一并中文化。
校验用的 ``ValueError`` 文案会随 422 响应返回给客户端，保持英文。
"""

import re
from datetime import datetime

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
)

from app.schemas.base import BaseResponse


class Token(BaseModel):
    """访问凭证。

    Attributes:
        access_token: JWT 令牌
        token_type: 令牌类型，固定为 bearer
        expires_at: 过期时间
    """

    access_token: str = Field(..., description="JWT 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_at: datetime = Field(..., description="令牌过期时间")


class TokenResponse(BaseResponse):
    """登录接口的响应体。

    Attributes:
        access_token: JWT 令牌
        token_type: 令牌类型，固定为 bearer
        expires_at: 过期时间
    """

    access_token: str = Field(..., description="JWT 访问令牌")
    token_type: str = Field(default="bearer", description="令牌类型")
    expires_at: datetime = Field(..., description="令牌过期时间")


class UserCreate(BaseModel):
    """注册请求体。

    Attributes:
        email: 邮箱
        password: 密码，8~64 位且需满足强度要求
        username: 可选显示名，最长 50 字
    """

    email: EmailStr = Field(..., description="用户邮箱")
    password: SecretStr = Field(..., description="用户密码", min_length=8, max_length=64)
    username: str | None = Field(default=None, description="可选显示名", max_length=50)

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: SecretStr) -> SecretStr:
        """校验密码强度。

        规则：至少 8 位，且同时包含大写字母、小写字母、数字与特殊字符。

        Args:
            v: 待校验的密码

        Returns:
            SecretStr: 校验通过的密码

        Raises:
            ValueError: 密码不满足任一强度要求
        """
        password = v.get_secret_value()

        # 逐条检查常见密码要求
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long")

        if not re.search(r"[A-Z]", password):
            raise ValueError("Password must contain at least one uppercase letter")

        if not re.search(r"[a-z]", password):
            raise ValueError("Password must contain at least one lowercase letter")

        if not re.search(r"[0-9]", password):
            raise ValueError("Password must contain at least one number")

        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
            raise ValueError("Password must contain at least one special character")

        return v


class UserResponse(BaseResponse):
    """用户相关接口的响应体。

    Attributes:
        id: 用户 ID
        email: 邮箱
        username: 可选显示名
        token: 登录凭证
    """

    id: int = Field(..., description="用户 ID")
    email: str = Field(..., description="用户邮箱")
    username: str | None = Field(default=None, description="可选显示名")
    token: Token = Field(..., description="访问凭证")


class SessionResponse(BaseResponse):
    """会话接口的响应体。

    Attributes:
        session_id: 会话唯一标识
        name: 会话名，默认空串
        token: 会话级凭证
    """

    session_id: str = Field(..., description="会话唯一标识")
    name: str = Field(default="", description="会话名称", max_length=100)
    token: Token = Field(..., description="会话访问凭证")

    @field_validator("name")
    @classmethod
    def sanitize_name(cls, v: str) -> str:
        """清洗会话名，去掉可能用于注入的字符。

        Args:
            v: 原始会话名

        Returns:
            str: 清洗后的会话名
        """
        # 去掉尖括号、花括号、引号、反引号等可能有害的字符
        sanitized = re.sub(r'[<>{}[\]()\'"`]', "", v)
        return sanitized
