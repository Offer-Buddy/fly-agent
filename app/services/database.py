"""数据库服务：用户与会话的增删改查。

用 SQLModel（SQLAlchemy + pydantic）做 ORM，维护一个同步连接池。
注意这里是**同步**引擎：FastAPI 的 handler 虽然是 async，但数据库操作用的是
SQLModel 的同步 Session，量级不大时够用；要换成全异步需改 asyncpg 版本。

注意：``HTTPException`` 的 detail 属于对外契约，保持英文。
"""

from typing import (
    List,
    Optional,
)
from urllib.parse import quote_plus

from fastapi import HTTPException
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.pool import QueuePool
from sqlmodel import (
    Session,
    col,
    create_engine,
    select,
)

from app.core.config import (
    Environment,
    settings,
)
from app.core.logging import logger
from app.models.session import Session as ChatSession
from app.models.user import User


class DatabaseService:
    """数据库操作服务。

    负责用户、会话与消息的持久化，内部持有一个连接池，
    所有方法都用 ``with Session(self.engine)`` 管理事务生命周期。
    """

    def __init__(self):
        """按配置创建数据库连接池。"""
        try:
            pool_size = settings.POSTGRES_POOL_SIZE
            max_overflow = settings.POSTGRES_MAX_OVERFLOW

            connection_url = (
                "postgresql://"
                f"{quote_plus(settings.POSTGRES_USER)}:{quote_plus(settings.POSTGRES_PASSWORD)}"
                f"@{settings.POSTGRES_HOST}:{settings.POSTGRES_PORT}/{settings.POSTGRES_DB}"
            )

            self.engine = create_engine(
                connection_url,
                # 取连接前先探活，避免拿到已被数据库单方面断开的连接
                pool_pre_ping=True,
                poolclass=QueuePool,
                pool_size=pool_size,
                max_overflow=max_overflow,
                pool_timeout=30,  # 取连接最长等待（秒）
                pool_recycle=1800,  # 连接最多复用 30 分钟，避开服务端空闲断开
            )

            logger.info(
                "database_initialized",
                environment=settings.ENVIRONMENT.value,
                pool_size=pool_size,
                max_overflow=max_overflow,
            )
        except SQLAlchemyError as e:
            logger.error("database_initialization_error", error=str(e), environment=settings.ENVIRONMENT.value)
            # 生产环境不抛：即使数据库暂时不可用也让进程起来，
            # 由 /health 探活暴露问题；其他环境直接崩掉更利于排错。
            if settings.ENVIRONMENT != Environment.PRODUCTION:
                raise

    async def create_user(self, email: str, password: str, username: str | None = None) -> User:
        """创建用户。

        Args:
            email: 邮箱
            password: 已哈希的密码
            username: 可选的显示名

        Returns:
            User: 创建好的用户
        """
        with Session(self.engine) as session:
            user = User(email=email, hashed_password=password, username=username)
            session.add(user)
            session.commit()
            session.refresh(user)
            logger.info("user_created", email=email)
            return user

    async def get_user(self, user_id: int) -> Optional[User]:
        """按 ID 查用户。

        Args:
            user_id: 用户 ID

        Returns:
            Optional[User]: 找到则返回用户，否则 None
        """
        with Session(self.engine) as session:
            user = session.get(User, user_id)
            return user

    async def get_user_by_email(self, email: str) -> Optional[User]:
        """按邮箱查用户。

        Args:
            email: 邮箱

        Returns:
            Optional[User]: 找到则返回用户，否则 None
        """
        with Session(self.engine) as session:
            statement = select(User).where(User.email == email)
            user = session.exec(statement).first()
            return user

    async def delete_user_by_email(self, email: str) -> bool:
        """按邮箱删除用户。

        Args:
            email: 邮箱

        Returns:
            bool: 删除成功 True，用户不存在 False
        """
        with Session(self.engine) as session:
            user = session.exec(select(User).where(User.email == email)).first()
            if not user:
                return False

            session.delete(user)
            session.commit()
            logger.info("user_deleted", email=email)
            return True

    async def create_session(
        self, session_id: str, user_id: int, name: str = "", username: str | None = None
    ) -> ChatSession:
        """创建聊天会话。

        Args:
            session_id: 新会话的 ID
            user_id: 归属用户 ID
            name: 会话名，默认空串（后续由自动命名逻辑填充）
            username: 从用户表冗余一份显示名，供 LLM 个性化使用

        Returns:
            ChatSession: 创建好的会话
        """
        with Session(self.engine) as session:
            chat_session = ChatSession(id=session_id, user_id=user_id, name=name, username=username)
            session.add(chat_session)
            session.commit()
            session.refresh(chat_session)
            logger.info("session_created", session_id=session_id, user_id=user_id, name=name)
            return chat_session

    async def delete_session(self, session_id: str) -> bool:
        """按 ID 删除会话。

        Args:
            session_id: 会话 ID

        Returns:
            bool: 删除成功 True，会话不存在 False
        """
        with Session(self.engine) as session:
            chat_session = session.get(ChatSession, session_id)
            if not chat_session:
                return False

            session.delete(chat_session)
            session.commit()
            logger.info("session_deleted", session_id=session_id)
            return True

    async def get_session(self, session_id: str) -> Optional[ChatSession]:
        """按 ID 查会话。

        Args:
            session_id: 会话 ID

        Returns:
            Optional[ChatSession]: 找到则返回会话，否则 None
        """
        with Session(self.engine) as session:
            chat_session = session.get(ChatSession, session_id)
            return chat_session

    async def get_user_sessions(self, user_id: int) -> List[ChatSession]:
        """列出某个用户的全部会话，按创建时间升序。

        Args:
            user_id: 用户 ID

        Returns:
            List[ChatSession]: 会话列表
        """
        with Session(self.engine) as session:
            statement = (
                select(ChatSession).where(col(ChatSession.user_id) == user_id).order_by(col(ChatSession.created_at))
            )
            sessions = session.exec(statement).all()
            return list(sessions)

    async def update_session_name(self, session_id: str, name: str) -> ChatSession:
        """修改会话名。

        Args:
            session_id: 会话 ID
            name: 新名称

        Returns:
            ChatSession: 更新后的会话

        Raises:
            HTTPException: 会话不存在时返回 404
        """
        with Session(self.engine) as session:
            chat_session = session.get(ChatSession, session_id)
            if not chat_session:
                raise HTTPException(status_code=404, detail="Session not found")

            chat_session.name = name
            session.add(chat_session)
            session.commit()
            session.refresh(chat_session)
            logger.info("session_name_updated", session_id=session_id, name=name)
            return chat_session

    def get_session_maker(self):
        """返回一个数据库会话对象（供需要自行控制事务的调用方使用）。

        Returns:
            Session: SQLModel 会话
        """
        return Session(self.engine)

    async def health_check(self) -> bool:
        """探测数据库连通性。

        Returns:
            bool: 健康返回 True，否则 False
        """
        try:
            with Session(self.engine) as session:
                # 跑一条最轻量的查询确认连接可用
                session.exec(select(1)).first()
                return True
        except Exception as e:
            logger.error("database_health_check_failed", error=str(e))
            return False


# 全局单例：整个进程共用一套连接池
database_service = DatabaseService()
