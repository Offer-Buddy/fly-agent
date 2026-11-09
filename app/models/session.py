"""聊天会话模型。"""

from typing import (
    TYPE_CHECKING,
    Optional,
)

from sqlmodel import (
    Field,
    Relationship,
)

from app.models.base import BaseModel

if TYPE_CHECKING:
    from app.models.user import User


class Session(BaseModel, table=True):
    """会话表：一次连续对话对应一条记录。

    Attributes:
        id: 主键，用 UUID 字符串（与 LangGraph 的 thread_id 一致）
        user_id: 外键，指向所属用户
        name: 会话名，默认空串，由自动命名逻辑填充
        username: 创建时从用户表冗余的显示名，供 LLM 个性化使用
        created_at: 创建时间（继承自 BaseModel）
        user: 反向关联所属用户
    """

    id: str = Field(primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    name: str = Field(default="")
    username: Optional[str] = Field(default=None)
    user: "User" = Relationship(back_populates="sessions")
