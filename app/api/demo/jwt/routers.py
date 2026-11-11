"""五组路由，演示「鉴权挂在哪一层」的四种位置与两个反例。

对照着看：

============================  ==========================================
位置                          生效范围
============================  ==========================================
不挂                          一个接口都不保护
端点参数 ``Depends(f)``        只有写了的那个接口
``APIRouter(dependencies=)``  这个 router 下所有接口，**且无法被端点取消**
``include_router(deps=)``     挂载时统一加，适合整块外来路由
============================  ==========================================

生产仓库选的是第一、二种：``auth.py`` 用端点级依赖，``chatbot.py`` 用路由级。
没有用全局中间件，因为注册与登录接口必须在「还没有身份」时被调用。
"""

from fastapi import (
    APIRouter,
    Depends,
)

from app.api.demo.jwt.deps import (
    SESSIONS,
    USERS,
    optional_user,
    require_session,
    require_user,
)
from app.utils.auth import create_access_token

# ---------------------------------------------------------------------------
# 一、公开组：一个依赖都不挂
# 判断标准只有一条——这个接口是不是在「拿到身份之前」被调用
# ---------------------------------------------------------------------------
public_router = APIRouter(tags=["1 公开：不验"])


@public_router.get("/ping")
async def ping() -> dict[str, str]:
    """探活接口。

    负载均衡、容器编排、监控都靠它判断实例死活。给它加鉴权等于让探活失败，
    随后整批实例被摘掉——这类接口必须公开。

    Returns:
        dict: 固定返回 pong
    """
    return {"message": "pong"}


@public_router.post("/login")
async def login() -> dict[str, str]:
    """登录，签发用户令牌。

    demo 里省掉了密码校验，只演示「这里必须公开」。真实实现在
    ``app/api/v1/auth.py`` 的 ``login_user``，它做密码哈希比对、限流、日志埋点。

    Returns:
        dict: 令牌与过期时间
    """
    token = create_access_token("1")
    return {"access_token": token.access_token, "token_type": "bearer", "scope": "user"}


@public_router.get("/feed")
async def feed(user: dict[str, object] | None = Depends(optional_user)) -> dict[str, object]:
    """可选登录：带了令牌显示昵称，不带也能看。

    这是「加了一半」的接口。它同时属于两档，所以判断标准不是非黑即白：
    **读接口可以半加，写接口不行**。令牌过期时这里静默降级成匿名，
    客户端不会知道该去重新登录。

    Args:
        user: 当前用户，未登录或令牌无效时为 None

    Returns:
        dict: 推荐列表与当前身份
    """
    return {
        "greeting": f"{user['name']}，欢迎回来" if user else "你好，游客",
        "items": ["内容一", "内容二"],
        "identity": "authenticated" if user else "anonymous",
    }


# ---------------------------------------------------------------------------
# 二、用户令牌组：端点级依赖，精确到单个函数
# 这一组里每个接口签名上都写着 Depends(require_user)，一眼能数出来
# ---------------------------------------------------------------------------
user_router = APIRouter(tags=["2 用户令牌：端点级"])


@user_router.get("/me")
async def me(user: dict[str, object] = Depends(require_user)) -> dict[str, object]:
    """返回当前用户。

    Args:
        user: 由 require_user 解析出的用户

    Returns:
        dict: 用户信息
    """
    return {"user": user}


@user_router.get("/sessions")
async def list_sessions(user: dict[str, object] = Depends(require_user)) -> dict[str, object]:
    """列出「我」的会话，不是所有人的。

    鉴权只解决「你是谁」，授权才解决「你能看哪些」。这里靠 ``user_id`` 过滤，
    漏掉这一句，接口照样能通过鉴权，但会泄露别人的数据——这是越权，不是鉴权漏洞。

    Args:
        user: 当前用户

    Returns:
        dict: 属于该用户的会话列表
    """
    mine = [s for s in SESSIONS.values() if s["user_id"] == user["id"]]
    return {"total": len(mine), "items": mine}


@user_router.post("/session-token")
async def issue_session_token(user: dict[str, object] = Depends(require_user)) -> dict[str, str]:
    """签发会话令牌：必须**先有用户令牌**才能换。

    这条接口本身就是「哪些要加鉴权」的答案：签发下级凭证的动作，一定要挂上级
    凭证的依赖。真实实现在 ``app/api/v1/auth.py`` 的 ``create_session``。

    Args:
        user: 当前用户

    Returns:
        dict: 会话令牌，``sub`` 是 session_id 而不是 user_id
    """
    session_id = "sess-001" if user["id"] == 1 else "sess-009"
    token = create_access_token(session_id)
    return {"access_token": token.access_token, "token_type": "bearer", "scope": "session"}


# ---------------------------------------------------------------------------
# 三、会话令牌组：路由级依赖，一行管一片
# 这里的关键差别——不是每个函数都写 Depends，而是构造 router 时就定了
# ---------------------------------------------------------------------------
session_router = APIRouter(
    dependencies=[Depends(require_session)],
    tags=["3 会话令牌：路由级"],
)


@session_router.post("/chat")
async def chat(session: dict[str, object] = Depends(require_session)) -> dict[str, object]:
    """发消息。注意这里又写了一次 Depends，是为了拿到对象本身。

    路由级依赖只负责「拦」，不给你返回值；要拿到当前会话，端点里还得再声明一次。
    两次声明不会验两遍——同一请求内 FastAPI 默认缓存依赖结果（``use_cache=True``）。

    Args:
        session: 当前会话

    Returns:
        dict: 模拟回复
    """
    return {"session_id": session["id"], "reply": "收到"}


@session_router.get("/messages")
async def messages() -> dict[str, object]:
    """拉历史消息。这个函数里没有任何鉴权代码，但它同样被保护。

    Returns:
        dict: 模拟历史消息
    """
    return {"total": 2, "items": [{"role": "user", "content": "你好"}]}


@session_router.delete("/messages")
async def clear_messages() -> dict[str, str]:
    """清空历史。分组就是这样：新加接口不会漏，因为保护在 router 上。

    Returns:
        dict: 操作结果
    """
    return {"message": "cleared"}


# ---------------------------------------------------------------------------
# 四、反例一：路由级依赖无法被端点取消
# ---------------------------------------------------------------------------
trap_router = APIRouter(
    dependencies=[Depends(require_user)],
    tags=["4 反例：取消不掉"],
)


@trap_router.get("/cannot-cancel")
async def cannot_cancel() -> dict[str, str]:
    """端点写了 ``dependencies=[]`` 想摘掉路由级依赖，**做不到**。

    装饰器参数里的 ``dependencies`` 只能追加，不能覆盖上一层。实际请求会先跑
    router 上的 require_user，没令牌直接 401，函数体根本进不来。
    想公开一个接口，唯一的办法是把它放进另一个 router。

    Returns:
        dict: 正常情况进不来
    """
    return {"message": "你看到了这行，说明未生效"}


# ---------------------------------------------------------------------------
# 五、反例二：漏挂依赖 —— 最危险的一种
# ---------------------------------------------------------------------------
forgot_router = APIRouter(tags=["5 反例：漏挂"])


@forgot_router.get("/user-secrets")
async def forgot_to_guard() -> dict[str, object]:
    """本该保护却忘了挂依赖：接口 200，谁都能看。

    这类疏漏不会报错、不会告警，测试不覆盖就永远发现不了。靠人眼 review
    不可靠，所以 ``audit.py`` 扫描全部路由，把「没挂鉴权」的接口列出来。

    Returns:
        dict: 本不该公开的数据
    """
    return {"leaked": [{"user_id": 1, "email": "captain@example.com"}]}


# ---------------------------------------------------------------------------
# 六、外来模块：源码里一个 Depends 都没有，靠挂载时补
# ---------------------------------------------------------------------------
legacy_router = APIRouter(tags=["6 挂载时补：include_router"])


@legacy_router.get("/reports")
async def reports() -> dict[str, object]:
    """模拟「别人写的、不想改」的老模块。

    函数签名干净，没有鉴权痕迹；保护来自 ``app.py`` 那句
    ``include_router(legacy_router, dependencies=[Depends(require_user)])``。

    Returns:
        dict: 报表数据
    """
    return {"reports": ["周报", "月报"]}


__all__ = [
    "USERS",
    "forgot_router",
    "legacy_router",
    "public_router",
    "session_router",
    "trap_router",
    "user_router",
]
