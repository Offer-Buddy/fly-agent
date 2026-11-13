"""用户模型。"""

from typing import (
    TYPE_CHECKING,
    List,
    Optional,
)

import bcrypt
from sqlmodel import (
    Field,
    Relationship,
)

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.session import Session


class User(BaseModel, table=True):
    """用户账户表。

    Attributes:
        id: 主键
        email: 邮箱，唯一索引
        hashed_password: bcrypt 哈希后的密码（永不存明文）
        username: 可选显示名
        created_at: 创建时间（继承自 BaseModel）
        sessions: 反向关联该用户的全部会话
    """

    id: int = Field(default=None, primary_key=True)
    email: str = Field(unique=True, index=True)
    sex: str = Field(default=None, index=True)
    hashed_password: str
    username: Optional[str] = Field(default=None, index=False)
    sessions: List["Session"] = Relationship(back_populates="user")

    def verify_password(self, password: str) -> bool:
        """校验明文密码是否与存储的哈希匹配。

        Args:
            password: 用户提交的明文密码

        Returns:
            bool: 匹配返回 True
        """
        return bcrypt.checkpw(password.encode("utf-8"), self.hashed_password.encode("utf-8"))

    @staticmethod
    def hash_password(password: str) -> str:
        """用 bcrypt 生成密码哈希。

        Args:
            password: 明文密码

        Returns:
            str: 可直接入库的哈希串
        """
        salt = bcrypt.gensalt()
        return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


# 放在文件末尾导入以避免循环依赖（models 之间互相引用）
from app.models.session import Session  # noqa: E402
