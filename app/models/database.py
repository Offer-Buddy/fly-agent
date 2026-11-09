"""数据库模型汇总出口。

Alembic 与建表逻辑都从这里取模型，新增表记得加进 ``__all__``，
否则自动迁移扫不到它。
"""

from app.models.thread import Thread

__all__ = ["Thread"]
