"""鉴权与授权接口。

覆盖用户注册、登录、会话创建 / 改名 / 删除、会话列表，以及两个供其他路由
复用的依赖：``get_current_user``（按用户 token 校验）与
``get_current_session``（按会话 token 校验）。

注意：``HTTPException`` 的 ``detail`` 会原样返回给客户端，属于对外契约，
因此保持英文，不要翻译。
"""

import uuid
from typing import List

from fastapi import (
    APIRouter,
    Depends,
    Form,
    HTTPException,
    Request,
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import (
    bind_context,
    logger,
)
from app.models.session import Session
from app.models.user import User
from app.schemas.auth import (
    SessionResponse,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from app.services.database import database_service
from app.utils.auth import (
    create_access_token,
    verify_token,
)
from app.utils.sanitization import (
    sanitize_email,
    sanitize_string,
    validate_password_strength,
)

router = APIRouter()
security = HTTPBearer()
# 复用共享实例：DatabaseService.__init__ 会自己建引擎，
# 在这里新建一个实例等于多出一套独立连接池。
db_service = database_service


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> User:
    """从 token 解析出当前用户。

    Args:
        credentials: 携带 JWT 的 HTTP 鉴权凭证

    Returns:
        User: token 对应的用户

    Raises:
        HTTPException: token 无效、缺失或对应用户不存在
    """
    try:
        # 先清洗 token，防止超长或含控制字符的输入
        token = sanitize_string(credentials.credentials)

        user_id = verify_token(token)
        if user_id is None:
            logger.error("invalid_token", token_part=token[:10] + "...")
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 确认用户仍然存在（token 可能签发于删除之前）
        user_id_int = int(user_id)
        user = await db_service.get_user(user_id_int)
        if user is None:
            logger.error("user_not_found", user_id=user_id_int)
            raise HTTPException(
                status_code=404,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 把 user_id 绑到日志上下文，本次请求后续日志都会带上
        bind_context(user_id=user_id_int)

        return user
    except ValueError as ve:
        logger.exception("token_validation_failed", error=str(ve))
        raise HTTPException(
            status_code=422,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_session(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> Session:
    """从 token 解析出当前会话。

    Args:
        credentials: 携带 JWT 的 HTTP 鉴权凭证

    Returns:
        Session: token 对应的会话

    Raises:
        HTTPException: token 无效、缺失或对应会话不存在
    """
    try:
        # 先清洗 token
        token = sanitize_string(credentials.credentials)

        session_id = verify_token(token)
        if session_id is None:
            logger.error("session_id_not_found", token_part=token[:10] + "...")
            raise HTTPException(
                status_code=401,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # session_id 也要清洗后再入库查询
        session_id = sanitize_string(session_id)

        # 确认会话仍然存在
        session = await db_service.get_session(session_id)
        if session is None:
            logger.error("session_not_found", session_id=session_id)
            raise HTTPException(
                status_code=404,
                detail="Session not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # 把 user_id 绑到日志上下文
        bind_context(user_id=session.user_id)

        return session
    except ValueError as ve:
        logger.exception("token_validation_failed", error=str(ve))
        raise HTTPException(
            status_code=422,
            detail="Invalid token format",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/register", response_model=UserResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["register"][0])
async def register_user(request: Request, user_data: UserCreate):
    """注册新用户。

    Args:
        request: FastAPI 请求对象，限流器需要
        user_data: 注册信息（邮箱、密码、可选用户名）

    Returns:
        UserResponse: 创建成功的用户信息与访问 token
    """
    try:
        # 规范化邮箱
        sanitized_email = sanitize_email(user_data.email)

        # 取出密码并校验强度
        password = user_data.password.get_secret_value()
        validate_password_strength(password)

        # 邮箱不能重复
        if await db_service.get_user_by_email(sanitized_email):
            raise HTTPException(status_code=400, detail="Email already registered")

        # 用户名可选，存在则一并清洗
        sanitized_username = sanitize_string(user_data.username) if user_data.username else None

        # 落库（密码只存哈希）
        user = await db_service.create_user(
            email=sanitized_email,
            password=User.hash_password(password),
            username=sanitized_username,
        )

        # 注册即登录，直接签发 token
        token = create_access_token(str(user.id))

        return UserResponse(id=user.id, email=user.email, username=user.username, token=token)
    except ValueError as ve:
        logger.exception("user_registration_validation_failed", error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))


@router.post("/login", response_model=TokenResponse)
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["login"][0])
async def login(
    request: Request, email: str = Form(...), password: str = Form(...), grant_type: str = Form(default="password")
):
    """用户登录。

    沿用 OAuth2 的表单字段风格（``grant_type`` / ``username`` / ``password``），
    方便前端直接用标准表单提交。

    Args:
        request: FastAPI 请求对象，限流器需要
        email: 登录邮箱
        password: 登录密码
        grant_type: 必须为 "password"

    Returns:
        TokenResponse: 访问 token 及其过期时间

    Raises:
        HTTPException: 凭证无效或 grant_type 不受支持
    """
    try:
        # 清洗输入；邮箱统一小写
        email = sanitize_string(email).lower()
        grant_type = sanitize_string(grant_type)

        # 只支持密码模式
        if grant_type != "password":
            raise HTTPException(
                status_code=400,
                detail="Unsupported grant type. Must be 'password'",
            )

        # 用户不存在与密码错误返回同一个提示，避免暴露账号是否注册
        user = await db_service.get_user_by_email(email)
        if not user or not user.verify_password(password):
            raise HTTPException(
                status_code=401,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = create_access_token(str(user.id))
        return TokenResponse(access_token=token.access_token, token_type="bearer", expires_at=token.expires_at)
    except ValueError as ve:
        logger.exception("login_validation_failed", error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))


@router.post("/session", response_model=SessionResponse)
async def create_session(user: User = Depends(get_current_user)):
    """为已登录用户新建一个聊天会话。

    Args:
        user: 当前已鉴权用户

    Returns:
        SessionResponse: 会话 ID、会话名与新签发的会话 token
    """
    try:
        # 会话 ID 用 UUID，避免可枚举
        session_id = str(uuid.uuid4())

        # 落库时冗余存一份用户名，供后续 LLM 个性化使用
        session = await db_service.create_session(session_id, user.id, username=user.username)

        # 会话级别的 token：后续聊天接口用它鉴权
        token = create_access_token(session_id)

        logger.info(
            "session_created",
            session_id=session_id,
            user_id=user.id,
            name=session.name,
            expires_at=token.expires_at.isoformat(),
        )

        return SessionResponse(session_id=session_id, name=session.name, token=token)
    except ValueError as ve:
        logger.exception("session_creation_validation_failed", error=str(ve), user_id=user.id)
        raise HTTPException(status_code=422, detail=str(ve))


@router.patch("/session/{session_id}/name", response_model=SessionResponse)
async def update_session_name(
    session_id: str, name: str = Form(...), current_session: Session = Depends(get_current_session)
):
    """修改会话名称。

    Args:
        session_id: 要修改的会话 ID
        name: 新名称
        current_session: 鉴权得到的当前会话

    Returns:
        SessionResponse: 更新后的会话信息与重新签发的 token
    """
    try:
        # 清洗全部输入
        sanitized_session_id = sanitize_string(session_id)
        sanitized_name = sanitize_string(name)
        sanitized_current_session = sanitize_string(current_session.id)

        # 只能改自己的会话
        if sanitized_session_id != sanitized_current_session:
            raise HTTPException(status_code=403, detail="Cannot modify other sessions")

        # 更新名称
        session = await db_service.update_session_name(sanitized_session_id, sanitized_name)

        # 重新签发 token：不是必需，但让客户端拿到的凭证保持一致
        token = create_access_token(sanitized_session_id)

        return SessionResponse(session_id=sanitized_session_id, name=session.name, token=token)
    except ValueError as ve:
        logger.exception("session_update_validation_failed", error=str(ve), session_id=session_id)
        raise HTTPException(status_code=422, detail=str(ve))


@router.delete("/session/{session_id}")
async def delete_session(session_id: str, current_session: Session = Depends(get_current_session)):
    """删除会话。

    Args:
        session_id: 要删除的会话 ID
        current_session: 鉴权得到的当前会话

    Returns:
        None
    """
    try:
        # 清洗输入
        sanitized_session_id = sanitize_string(session_id)
        sanitized_current_session = sanitize_string(current_session.id)

        # 只能删自己的会话
        if sanitized_session_id != sanitized_current_session:
            raise HTTPException(status_code=403, detail="Cannot delete other sessions")

        await db_service.delete_session(sanitized_session_id)

        logger.info("session_deleted", session_id=session_id, user_id=current_session.user_id)
    except ValueError as ve:
        logger.exception("session_deletion_validation_failed", error=str(ve), session_id=session_id)
        raise HTTPException(status_code=422, detail=str(ve))


@router.get("/sessions", response_model=List[SessionResponse])
async def get_user_sessions(user: User = Depends(get_current_user)):
    """列出当前用户的全部会话。

    Args:
        user: 当前已鉴权用户

    Returns:
        List[SessionResponse]: 会话列表，每项都带一个可直接使用的会话 token
    """
    try:
        sessions = await db_service.get_user_sessions(user.id)
        return [
            SessionResponse(
                session_id=sanitize_string(session.id),
                name=sanitize_string(session.name),
                token=create_access_token(session.id),
            )
            for session in sessions
        ]
    except ValueError as ve:
        logger.exception("get_sessions_validation_failed", user_id=user.id, error=str(ve))
        raise HTTPException(status_code=422, detail=str(ve))
