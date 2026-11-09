"""所有模型的公共基类。"""

from datetime import datetime, UTC
from sqlmodel import Field, SQLModel


class BaseModel(SQLModel):
    """带通用字段的模型基类。

    目前只有创建时间；所有时间戳统一用 UTC，避免部署到不同时区时出现偏移。
    """

    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
