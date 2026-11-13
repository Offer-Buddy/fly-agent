"""法律知识库的数据表模型：``law`` 与 ``law_chunk``。

与 ``app/services/rag/types.py`` 里的 ``LawChunk`` 是两个东西，别混：

- 这里的 ``LawChunkRow`` 是**数据库行**，负责建表与迁移
- 那里的 ``LawChunk`` 是**领域对象**，在检索链路里流转

故意不共用一套模型：领域对象以后可能要加打分、加来源追溯字段，
这些都不该变成表结构。表本身由 Alembic 迁移管理（见 ``alembic/versions``），
在这里定义一份是为了让 ``alembic revision --autogenerate`` 能看见它们。
"""

from datetime import (
    date,
    datetime,
)
from typing import Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import (
    Field,
    SQLModel,
)


class Law(SQLModel, table=True):
    """法规主表：一部法规一行。

    Attributes:
        id: 主键
        title: 官方全称
        short_name: 简称，唯一键。``chunk_id`` 由它拼成，写入时的 upsert 也靠它定位
        status: 效力状态，1 历史版本 / 2 已修改 / 3 现行有效 / 4 尚未生效
        publish_date: 公布日期
        effective_date: 施行日期
        issuer: 制定机关
        category: 法规类别
        version_id: 官方版本标识（bbbs），判断「有没有出新版」的唯一依据
        source: 数据来源
        updated_at: 最后更新时间
    """

    __tablename__ = "law"

    # 索引在模型里也要写一份：迁移里的 CREATE INDEX 一旦不被模型「认领」，
    # 下一次 autogenerate 就会把它们当成「库里多出来的」，生成一串 drop_index。
    __table_args__ = (Index("idx_law_status", "status"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str = Field(description="官方全称")
    short_name: str = Field(unique=True, description="简称，chunk_id 前缀")
    status: int = Field(description="1 历史版本 / 2 已修改 / 3 现行有效 / 4 尚未生效")
    publish_date: Optional[date] = None
    effective_date: Optional[date] = None
    issuer: Optional[str] = None
    category: Optional[str] = None
    version_id: Optional[str] = Field(default=None, unique=True)
    source: Optional[str] = None
    updated_at: Optional[datetime] = None


class LawChunkRow(SQLModel, table=True):
    """分块表：一条法条一行，检索的最小单元。

    ``status`` / ``effective_date`` 是从 ``law`` 表冗余下来的，这是本设计里
    唯一一处刻意的反规范化：HNSW 索引的 ``WHERE`` 条件只能引用本表列，
    不冗余就只能「先召回再过滤」，结果集会被历史版本稀释。

    Attributes:
        id: 主键
        law_id: 所属法规
        chunk_id: 全局唯一标识，如「民法典_第三百五十八条」
        law_name: 法规简称（冗余，免 join 就能按法名过滤）
        article_no: 条号，如「第三百五十八条」
        article_num: 条号数字，精确条号查询走这一列
        hierarchy: 层级路径
        content: 条号 + 正文，BM25 与精排都读这一份
        token_count: 分词后词数，用于排查切分异常
        status: 冗余自 ``law.status``
        effective_date: 冗余自 ``law.effective_date``
        embedding: 512 维向量（bge-small-zh-v1.5，入库前已归一化）
        tsv: 预留的全文索引列，当前检索链路不读它（内存 BM25 更快）
        created_at: 写入时间
    """

    __tablename__ = "law_chunk"

    # 五条索引的形状（HNSW 参数、部分索引条件、GIN）在这里声明，供 autogenerate 比对。
    # 不写这几行，它只会看到「库里有、模型里没有」——下一次生成就把它们删掉。
    __table_args__ = (
        Index(
            "idx_chunk_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
        ),
        Index(
            "idx_chunk_embedding_active",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_where=text("status = 3"),
        ),
        Index("idx_chunk_tsv", "tsv", postgresql_using="gin"),
        Index("idx_chunk_law_id", "law_id"),
        Index("idx_chunk_article", "law_name", "article_num"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    law_id: int = Field(foreign_key="law.id", ondelete="CASCADE", description="所属法规")
    chunk_id: str = Field(unique=True, description="全局唯一分块标识")
    law_name: str = Field(description="法规简称")
    article_no: str = Field(description="条号")
    article_num: Optional[int] = Field(default=None, description="条号数字")
    hierarchy: str = Field(default="", description="层级路径")
    content: str = Field(description="条号 + 正文")
    token_count: Optional[int] = None
    status: int = Field(description="冗余自 law.status")
    effective_date: Optional[date] = None
    embedding: list[float] = Field(sa_column=Column(Vector(512), nullable=False))
    tsv: Optional[str] = Field(default=None, sa_column=Column(TSVECTOR))
    created_at: Optional[datetime] = None
