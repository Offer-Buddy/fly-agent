"""Alembic 运行环境配置。

数据库连接串直接取自应用的 ``settings``，保证迁移用的库和运行时是同一个，
不会出现「迁移打到 A 库、服务连 B 库」的情况。
"""

from logging.config import fileConfig
from urllib.parse import quote_plus

from sqlalchemy import engine_from_config, pool
from sqlmodel import SQLModel

from alembic import context
from app.core.config import settings
from app.models.session import Session  # noqa: F401
from app.models.thread import Thread  # noqa: F401
from app.models.user import User  # noqa: F401

# Alembic 的配置对象
config = context.config

# 用 alembic.ini 里的配置初始化 Python 日志
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
# 从应用配置拼出数据库地址。凭据必须 URL 编码：
# 否则密码里的 "@" 或 "/" 会被当成主机名的一部分。
DATABASE_URL = (
    f"postgresql://{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
    f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
)
# set_main_option 走的是 configparser，百分号编码后的凭据必须把 "%" 转义成 "%%"，
# 否则会被当成变量插值而报错。
config.set_main_option("sqlalchemy.url", DATABASE_URL.replace("%", "%%"))

# 把 SQLModel 的元数据交给 Alembic，autogenerate 才能发现模型变更
target_metadata = SQLModel.metadata

# 这些表由外部组件创建和维护（LangGraph 检查点、mem0、pgvector），
# Alembic 一律不碰，否则会生成一堆删表迁移把别人的数据干掉。
EXCLUDE_TABLES = {
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
    "checkpoints",
    "longterm_memory",
    "mem0migrations",
}


def include_object(object, name, type_, reflected, compare_to):
    """过滤掉外部系统管理的表，让 autogenerate 忽略它们。

    Args:
        object: 待检查的对象
        name: 对象名
        type_: 对象类型（table / index / column ...）
        reflected: 是否来自数据库反射
        compare_to: 用于对比的元数据对象

    Returns:
        bool: 返回 False 表示不纳入本次迁移
    """
    if type_ == "table" and name in EXCLUDE_TABLES:
        return False
    return True


def run_migrations_offline() -> None:
    """以「离线」模式运行迁移。

    不连数据库，只把 SQL 打印到标准输出，便于人工审核或交给 DBA 执行。
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """以「在线」模式运行迁移，直接对目标库执行。"""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # 迁移是一次性任务，不需要连接池
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
