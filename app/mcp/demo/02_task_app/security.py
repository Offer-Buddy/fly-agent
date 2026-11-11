"""⑤ 网关：签发/校验 JWT + 按用户限流，一层罩住 /api 和 /mcp。

对应课件第五、六节。两个设计决定值得记住：

一、为什么不用 `@limiter.limit` 装饰器？
   装饰器只挂得到路由函数，挂不到 mount 进来的 MCP 子应用。
   想让两条通道共用一套规则，只能下沉到 ASGI 这一层，写一次就够。

二、为什么不用 BaseHTTPMiddleware？
   MCP 的响应常常是 SSE 流，纯 ASGI 中间件只透传消息、不碰响应体，
   不会把流缓冲掉。
"""

import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from starlette.datastructures import Headers
from starlette.responses import JSONResponse

from .config import ALGORITHM, EXPIRE_MINUTES, QUOTA, SECRET_KEY, WINDOW

# 不经过网关的路径：探活 + 登录换 token + 文档
PUBLIC_PATHS = {"/health", "/api/token", "/docs", "/openapi.json", "/redoc", "/favicon.ico"}


def issue_token(username: str) -> str:
    """签发 token，把用户名放进 sub。

    Args:
        username: 要签给谁。

    Returns:
        签名后的 JWT 字符串。
    """
    payload = {
        "sub": username,  # sub = 这张票代表谁
        "exp": datetime.now(UTC) + timedelta(minutes=EXPIRE_MINUTES),  # exp = 何时作废
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)  # 用密钥签名


def user_of(token: str) -> str | None:
    """验签并取出用户名，验不过返回 None。

    Args:
        token: 待校验的 JWT。

    Returns:
        用户名；签名不对、过期或格式坏掉时返回 None。
    """
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:  # 签名错、过期、格式坏，都归这一处
        return None
    subject = payload.get("sub")
    return subject if isinstance(subject, str) else None


def bearer_of(scope: dict) -> str:
    """从请求头里抠出 Bearer token。

    Args:
        scope: ASGI scope。

    Returns:
        去掉 `Bearer ` 前缀的 token；没有就返回空字符串。
    """
    auth = Headers(scope=scope).get("authorization", "")
    return auth[7:].strip() if auth.lower().startswith("bearer ") else ""


# ── 滑动窗口 ──────────────────────────────────────────────────────────
# 每个「用户 + 通道」一个桶：额度跟着人走，不跟着 IP 走。
# 同一张票打满 REST 额度，MCP 通道还能继续用 —— 这是故意的。
_hits: dict[str, deque[float]] = defaultdict(deque)  # 每个桶存一串时间戳


def _take(bucket: str, quota: int, now: float) -> float:
    """记一笔额度，超了不让走。

    Args:
        bucket: 桶名，形如 `alice|/api`。
        quota: 这个桶允许的次数。
        now: 当前时间戳。

    Returns:
        放行返回 0；被限流返回还要等几秒。
    """
    q = _hits[bucket]
    while q and now - q[0] >= WINDOW:  # 丢掉滑出窗口的旧记录
        q.popleft()
    if len(q) >= quota:  # 窗口内已经用满
        return WINDOW - (now - q[0])  # 差多久最老那条过期
    q.append(now)  # 没满就记一笔
    return 0.0


class Gateway:
    """验签 → 限流 → 放行。顺序不能反：先知道是谁，限流才有意义。"""

    def __init__(self, app: Any) -> None:
        """包住下游 ASGI 应用。

        Args:
            app: 下游 ASGI 应用（FastAPI 本体或任意 ASGI 子应用）。
        """
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        """每个请求都从这里过。

        Args:
            scope: ASGI 连接信息。
            receive: 收消息的可调用对象。
            send: 发消息的可调用对象。
        """
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:  # 非 HTTP 或白名单
            await self.app(scope, receive, send)  # 探活、换票、文档放行
            return

        username = user_of(bearer_of(scope))  # 从头里抠票再验
        if username is None:  # 没票，或票是假的
            await self._deny(send, 401, "invalid_token", "缺少或无效的 Bearer token")
            return

        channel = "/mcp" if scope["path"].startswith("/mcp") else "/api"  # 按路径归通道
        wait = _take(f"{username}|{channel}", QUOTA[channel], time.monotonic())  # 桶名 = 人 + 通道
        if wait > 0:  # 超了就拒
            await self._deny(send, 429, "rate_limited", "请求太频繁", wait)
            return

        # 身份写进 scope：后面的路由函数能直接取到，不用再解一次 token
        scope.setdefault("state", {})["username"] = username
        await self.app(scope, receive, send)  # 放行到路由或 MCP 子应用

    @staticmethod
    async def _deny(send: Any, status: int, code: str, detail: str, retry_after: float = 0.0) -> None:
        """统一拒绝：返回 JSON 错误，被限流时带 retry-after。

        Args:
            send: 发消息的可调用对象。
            status: HTTP 状态码。
            code: 机器可读的错误码。
            detail: 给人看的说明。
            retry_after: 还要等几秒，0 表示不是限流。
        """
        headers = {"retry-after": str(max(1, int(retry_after)))} if retry_after else None
        response = JSONResponse({"error": code, "detail": detail}, status_code=status, headers=headers)
        await response({"type": "http"}, None, send)
