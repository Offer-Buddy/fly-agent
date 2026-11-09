"""对话线程模型。

注意：会话内容的实际存储在 LangGraph 的检查点表里，
这张表只保留线程本身的元信息（ID 与创建时间）。
"""

from datetime import (
    UTC,
    datetime,
)

from sqlmodel import (
    Field,
    SQLModel,
)


class Thread(SQLModel, table=True):
    """对话线程表。

    Attributes:
        id: 主键
        created_at: 创建时间（UTC）
    """

    id: str = Field(primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
