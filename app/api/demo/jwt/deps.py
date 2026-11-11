"""三档鉴权依赖。

鉴权不是一个开关，而是一把刻度：同一个应用里同时存在「完全不验」「验了但
不拦」「必须验」三种接口。本模块把三档都写出来，好让「哪些接口加、哪些不加」
有一个可以指认的对象。

与生产实现的差异：``app/api/v1/auth.py`` 里查的是数据库，这里查内存表，
只为让 demo 不依赖 PG 也能跑；校验逻辑（``verify_token``、错误码三分）与生产一致。
"""

from typing import Optional

from fastapi import (
    Depends,
    HTTPException,
    Request,
)
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
)

from app.utils.auth import verify_token

# auto_error=False：不让 HTTPBearer 自己抛。
# 默认的 HTTPBearer(auto_error=True) 在「没带 Authorization 头」时抛的是 403
# 而不是 401，客户端拿到的语义是「你没权限」而不是「你没登录」，容易误导。
bearer_scheme = HTTPBearer(auto_error=False)

# ---------------------------------------------------------------------------
# Demo 数据：生产环境这两张表在数据库里（app/services/database.py）
# ---------------------------------------------------------------------------
USERS: dict[int, dict[str, object]] = {
    1: {"id": 1, "email": "captain@example.com", "name": "船长"},
    2: {"id": 2, "email": "guest@example.com", "name": "访客"},
}

SESSIONS: dict[str, dict[str, object]] = {
    "sess-001": {"id": "sess-001", "user_id": 1, "name": "第一次对话"},
    "sess-002": {"id": "sess-002", "user_id": 1, "name": "第二次对话"},
    "sess-009": {"id": "sess-009", "user_id": 2, "name": "别人的对话"},
}


def unauthorized(detail: str) -> HTTPException:
    """构造 401：令牌没带、无效或过期。

    ``WWW-Authenticate: Bearer`` 是 RFC 6750 要求的响应头，缺了它有些客户端
    不会提示「需要登录」，浏览器开发者工具里也看不出这是鉴权失败。

    Args:
        detail: 对外文案，属于契约，保持英文

    Returns:
        HTTPException: 状态码 401 的异常
    """
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


def decode_subject(credentials: Optional[HTTPAuthorizationCredentials]) -> str:
    """取出令牌里的 ``sub``，不做查库。

    这里刻意把「解码」和「查库」分开：解码只解决「这枚令牌是不是我们签的」，
    查库才解决「它指的人/会话还在不在」。两者混在一起，就分不清 401 和 404。

    Args:
        credentials: HTTPBearer 解析出的凭证，可能为 None

    Returns:
        str: 令牌里的 sub

    Raises:
        HTTPException: 未携带令牌（401）或格式非法（422）
    """
    if credentials is None:
        raise unauthorized("Not authenticated")

    try:
        subject = verify_token(credentials.credentials)
    except ValueError:
        # 连格式都不对：这不是「你是谁」的问题，是「你给的不是令牌」
        raise HTTPException(status_code=422, detail="Invalid token format") from None

    if subject is None:
        # 格式对但验不过：签名不符、过期、换过密钥
        raise unauthorized("Invalid authentication credentials")

    return subject


async def require_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict[str, object]:
    """第一档：必须携带**用户令牌**，否则 401 / 404。

    这一档给「会话管理」用：创建、列举、改名、删除会话。这些动作的主体是
    「人」，所以 ``sub`` 必须是 user_id。

    Args:
        request: 用于把 user_id 写进 state，供日志中间件补全上下文
        credentials: HTTPBearer 解析出的凭证

    Returns:
        dict: 内存表里的用户记录

    Raises:
        HTTPException: 未携带 401、格式非法 422、用户已不存在 404
    """
    subject = decode_subject(credentials)

    try:
        user_id = int(subject)
    except ValueError:
        # sub 是 session_id 却拿去查用户表——典型症状就是这个 404 而不是 401，
        # 因为签名校验是过的，只是这一列查不到东西
        raise HTTPException(status_code=404, detail="User not found") from None

    user = USERS.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # 正确做法：依赖负责写 state，中间件负责读。生产仓库里这一步是空的
    # （middleware.py 读了 request.state.user_id，但没人写），见 README。
    request.state.user_id = user_id
    return user


async def require_session(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> dict[str, object]:
    """第二档：必须携带**会话令牌**，否则 401 / 404。

    这一档给「聊天」用：发消息、拉历史、清空。主体是「某个会话」而不是「人」，
    所以 ``sub`` 是 session_id。会话被删了，令牌即便没过期也要失效——这就是
    「每次查库」换来的撤销能力。

    Args:
        request: 用于把 user_id 写进 state
        credentials: HTTPBearer 解析出的凭证

    Returns:
        dict: 内存表里的会话记录

    Raises:
        HTTPException: 未携带 401、格式非法 422、会话已不存在 404
    """
    session_id = decode_subject(credentials)

    session = SESSIONS.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    request.state.user_id = session["user_id"]
    request.state.session_id = session_id
    return session


async def optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> Optional[dict[str, object]]:
    """第三档：验，但不拦。

    用于「登录了体验更好、没登录也能看」的接口（公开分享页、首页推荐）。
    注意这里的取舍：令牌无效时**静默降级成匿名**，而不是报错。
    坏处是客户端令牌过期了也发现不了，所以只适合读接口，绝不要用在写接口上。

    Args:
        credentials: HTTPBearer 解析出的凭证

    Returns:
        Optional[dict]: 用户记录；未登录或令牌无效时为 None
    """
    if credentials is None:
        return None

    try:
        subject = verify_token(credentials.credentials)
    except ValueError:
        return None

    if subject is None:
        return None

    try:
        return USERS.get(int(subject))
    except ValueError:
        return None
