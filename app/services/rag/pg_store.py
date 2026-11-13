"""pgvector 存储层 —— 法条分块与向量的读写。

**为什么用异步连接池，而不是照搬 Legal 的同步实现**：
Legal 的 ``PgVectorLawStore`` 是「单连接 + threading.Lock」，在同步脚本里没问题，
但一进 FastAPI 的 async 路由就会阻塞事件循环——单请求测不出来，一上并发整个
服务卡死。这里换成 psycopg3 的 ``AsyncConnectionPool``，所有 I/O 天然让出控制权。

**为什么向量以字符串传参再 ``::vector`` 转型**：省掉 ``pgvector.psycopg``
的注册步骤，连接池里怎么拿连接都不会漏注册，也不会因为版本差异踩坑。
500 个浮点数转字符串的开销，比一次注册失败难查得多。

表结构见 ``alembic/versions``：``law`` 是法规主表，``law_chunk`` 是分块表。
``status`` / ``effective_date`` 从主表冗余到分块表，是为了让 HNSW 的
``WHERE status = 3`` 只引用本表列，避免「先召回再过滤」被历史版本稀释。
"""

from __future__ import annotations

import asyncio
from typing import Optional
from urllib.parse import quote_plus

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.core.config import settings
from app.core.logging import logger
from app.services.rag.types import (
    LawChunk,
    LawMeta,
)

# 检索时取回的列，顺序与 _row_to_chunk 一一对应
_SELECT_COLUMNS = "chunk_id, law_name, article_no, hierarchy, content, article_num"


def to_vector_literal(embedding: list[float]) -> str:
    """把浮点数组转成 pgvector 的文本字面量。

    Args:
        embedding: 向量

    Returns:
        str: 形如 ``[0.013,-0.024,...]`` 的字符串，配合 ``%s::vector`` 使用
    """
    # 6 位小数足够：bge 输出本身精度有限，多写只是白占带宽
    return "[" + ",".join(f"{value:.6f}" for value in embedding) + "]"


def _row_to_chunk(row: dict) -> LawChunk:
    """把查询结果行转成 LawChunk。

    Args:
        row: psycopg 的字典行

    Returns:
        LawChunk: 分块对象
    """
    return LawChunk(
        chunk_id=row["chunk_id"],
        law_name=row["law_name"],
        article_no=row["article_no"],
        hierarchy=row["hierarchy"] or "",
        content=row["content"],
        article_num=row["article_num"],
    )


class PgLawStore:
    """法条分块的 PostgreSQL + pgvector 存储。

    连接池按需创建：第一次用时才建，避免仅导入模块就占连接。
    """

    def __init__(self) -> None:
        """初始化（不建连接）。"""
        self._pool: Optional[AsyncConnectionPool] = None
        self._lock = asyncio.Lock()

    @staticmethod
    def dsn() -> str:
        """拼接数据库 DSN。

        凭据必须 URL 编码，否则密码里的 ``@`` ``/`` 会被当成主机名的一部分。

        Returns:
            str: ``postgresql://user:pass@host:port/db``
        """
        return (
            "postgresql://"
            f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
            f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
        )

    async def _get_pool(self) -> AsyncConnectionPool:
        """获取（必要时创建）连接池。

        Returns:
            AsyncConnectionPool: 已打开的连接池
        """
        if self._pool is None:
            async with self._lock:
                if self._pool is None:
                    # 检索是短查询，池子不需要大：2~4 个连接足够撑住课堂级并发
                    pool = AsyncConnectionPool(
                        self.dsn(),
                        min_size=1,
                        max_size=4,
                        open=False,
                        kwargs={"autocommit": True, "connect_timeout": 5, "row_factory": dict_row},
                    )
                    await pool.open()
                    self._pool = pool
                    logger.info("law_store_pool_created", host=settings.POSTGRES_HOST, db=settings.POSTGRES_DB)
        return self._pool

    async def close(self) -> None:
        """关闭连接池（服务关停时调用）。"""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("law_store_pool_closed")

    async def health(self) -> bool:
        """探测表是否可用。

        Returns:
            bool: ``law_chunk`` 可查询时为 True
        """
        try:
            pool = await self._get_pool()
            async with pool.connection() as conn:
                await conn.execute("SELECT 1 FROM law_chunk LIMIT 1")
            return True
        except Exception as error:  # noqa: BLE001 - 健康检查不该抛出，只需给出 False
            logger.warning("law_store_unhealthy", error=str(error))
            return False

    async def count_chunks(self, only_active: bool = False) -> int:
        """统计分块数量。

        Args:
            only_active: 只统计现行有效版本

        Returns:
            int: 行数
        """
        sql = "SELECT count(*) AS total FROM law_chunk"
        if only_active:
            sql += " WHERE status = 3"
        pool = await self._get_pool()
        async with pool.connection() as conn:
            cursor = await conn.execute(sql)
            row = await cursor.fetchone()
        return int(row["total"]) if row else 0

    async def list_law_names(self) -> list[str]:
        """列出库里所有法规简称。

        Returns:
            list[str]: 法规简称列表，用于从查询里猜法名
        """
        pool = await self._get_pool()
        async with pool.connection() as conn:
            cursor = await conn.execute("SELECT short_name FROM law ORDER BY short_name")
            rows = await cursor.fetchall()
        return [row["short_name"] for row in rows]

    async def search_by_vector(
        self,
        embedding: list[float],
        top_k: int = 20,
        law_names: Optional[list[str]] = None,
    ) -> list[tuple[LawChunk, float]]:
        """向量检索（余弦相似度）。

        Args:
            embedding: 归一化后的查询向量
            top_k: 返回条数
            law_names: 只在这些法规里检索；为空表示全库

        Returns:
            list[tuple[LawChunk, float]]: (分块, 相似度)，相似度 = 1 - 余弦距离
        """
        vector = to_vector_literal(embedding)
        params: dict = {"vector": vector, "limit": top_k}
        condition = "status = 3"
        if law_names:
            condition += " AND law_name = ANY(%(names)s)"
            params["names"] = law_names

        sql = f"""
            SELECT {_SELECT_COLUMNS}, 1 - (embedding <=> %(vector)s::vector) AS similarity
            FROM law_chunk
            WHERE {condition}
            ORDER BY embedding <=> %(vector)s::vector
            LIMIT %(limit)s
        """
        pool = await self._get_pool()
        async with pool.connection() as conn:
            # ef_search 是「召回多少个候选」的旋钮：调大更准更慢。
            # 但它同时会抬高 pgvector 的代价估算——调太大（这张表上 100）
            # 规划器会直接放弃索引改走全表扫描，反而更慢。评测必须固定此值。
            await conn.execute(f"SET hnsw.ef_search = {int(settings.HNSW_EF_SEARCH)}")
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()

        return [(_row_to_chunk(row), float(row["similarity"])) for row in rows]

    async def find_by_article(
        self,
        article_num: int,
        law_names: Optional[list[str]] = None,
        limit: int = 3,
    ) -> list[LawChunk]:
        """按条号精确命中。

        「第 358 条」这类查询不该赌向量近似，直接走 ``(law_name, article_num)`` 索引，
        实测 0.04 ms。

        Args:
            article_num: 条号数字，如 358
            law_names: 限定法规；为空表示全库（可能出现多个法的同号条文）
            limit: 返回条数

        Returns:
            list[LawChunk]: 命中的分块
        """
        params: dict = {"num": article_num, "limit": limit}
        condition = "article_num = %(num)s AND status = 3"
        if law_names:
            condition += " AND law_name = ANY(%(names)s)"
            params["names"] = law_names

        sql = f"SELECT {_SELECT_COLUMNS} FROM law_chunk WHERE {condition} ORDER BY id LIMIT %(limit)s"
        pool = await self._get_pool()
        async with pool.connection() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
        return [_row_to_chunk(row) for row in rows]

    async def fetch_all_chunks(self, only_active: bool = True) -> list[LawChunk]:
        """取全量分块，用于构建内存 BM25 索引。

        Args:
            only_active: 只取现行有效版本

        Returns:
            list[LawChunk]: 全部分块
        """
        condition = "WHERE status = 3" if only_active else ""
        sql = f"SELECT {_SELECT_COLUMNS} FROM law_chunk {condition} ORDER BY id"
        pool = await self._get_pool()
        async with pool.connection() as conn:
            cursor = await conn.execute(sql)
            rows = await cursor.fetchall()
        return [_row_to_chunk(row) for row in rows]

    async def upsert_law(self, meta: LawMeta) -> int:
        """写入或更新一部法规，返回主键。

        Args:
            meta: 法规元信息

        Returns:
            int: ``law.id``
        """
        sql = """
            INSERT INTO law (title, short_name, status, publish_date, effective_date,
                             issuer, category, version_id, source, updated_at)
            VALUES (%(title)s, %(short_name)s, %(status)s, %(publish_date)s, %(effective_date)s,
                    %(issuer)s, %(category)s, %(version_id)s, %(source)s, now())
            ON CONFLICT (short_name) DO UPDATE SET
                title = EXCLUDED.title,
                status = EXCLUDED.status,
                publish_date = EXCLUDED.publish_date,
                effective_date = EXCLUDED.effective_date,
                issuer = EXCLUDED.issuer,
                category = EXCLUDED.category,
                version_id = EXCLUDED.version_id,
                updated_at = now()
            RETURNING id
        """
        params = {
            "title": meta.title,
            "short_name": meta.short_name,
            "status": meta.status,
            "publish_date": meta.publish_date,
            "effective_date": meta.effective_date,
            "issuer": meta.issuer,
            "category": meta.category,
            "version_id": meta.version_id,
            "source": meta.source,
        }
        pool = await self._get_pool()
        async with pool.connection() as conn:
            cursor = await conn.execute(sql, params)
            row = await cursor.fetchone()
        return int(row["id"])

    async def replace_law_chunks(
        self,
        law_id: int,
        chunks: list[LawChunk],
        embeddings: list[list[float]],
        status: int,
        effective_date=None,
        token_counts: Optional[list[int]] = None,
    ) -> int:
        """重建一部法规的全部分块（先删后插，保证幂等）。

        Args:
            law_id: ``law.id``
            chunks: 分块列表
            embeddings: 与 chunks 等长同序的向量
            status: 从主表冗余下来的效力状态
            effective_date: 从主表冗余下来的施行日期
            token_counts: 每个分块的 token 数，用于排查切分异常

        Returns:
            int: 写入行数
        """
        if len(chunks) != len(embeddings):
            raise ValueError(f"分块数({len(chunks)})与向量数({len(embeddings)})不一致")

        counts = token_counts or [None] * len(chunks)
        rows = [
            (
                law_id,
                chunk.chunk_id,
                chunk.law_name,
                chunk.article_no,
                chunk.article_num,
                chunk.hierarchy,
                chunk.content,
                count,
                status,
                effective_date,
                to_vector_literal(embedding),
                chunk.content,
            )
            for chunk, embedding, count in zip(chunks, embeddings, counts)
        ]

        insert_sql = """
            INSERT INTO law_chunk (law_id, chunk_id, law_name, article_no, article_num, hierarchy,
                                   content, token_count, status, effective_date, embedding, tsv)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::vector, to_tsvector('simple', %s))
        """

        pool = await self._get_pool()
        async with pool.connection() as conn:
            await conn.execute("DELETE FROM law_chunk WHERE law_id = %s", (law_id,))
            async with conn.cursor() as cursor:
                await cursor.executemany(insert_sql, rows)
        return len(rows)

    async def analyze(self) -> None:
        """刷新两张表的统计信息。

        批量写入后不做这一步，规划器会按旧的行数估算代价，
        向量查询可能被判定为「索引不如全表扫」而放弃 HNSW。
        """
        pool = await self._get_pool()
        async with pool.connection() as conn:
            await conn.execute("ANALYZE law_chunk")
            await conn.execute("ANALYZE law")
        logger.info("law_store_analyzed")

    async def clear(self) -> None:
        """清空全部法规与分块（重建索引前调用）。"""
        pool = await self._get_pool()
        async with pool.connection() as conn:
            # law_chunk 有 ON DELETE CASCADE，但从分块表删起更直观
            await conn.execute("DELETE FROM law_chunk")
            await conn.execute("DELETE FROM law")
        logger.info("law_store_cleared")


# 全局单例：整个进程共用一个连接池
law_store = PgLawStore()
