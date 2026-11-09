"""可观测性接入：Langfuse 链路追踪。

对外只暴露三件事：

- :func:`langfuse_init` —— 应用启动时建客户端并做一次鉴权探测；
- :func:`trace_attributes` —— 把 session_id / user_id / 标签挂到当前 trace 上；
- :func:`flush` —— 进程退出前强制上报，命令行脚本必须调用。

关掉 ``LANGFUSE_TRACING_ENABLED`` 后不创建客户端、不挂回调，
业务侧调用 :func:`trace_attributes` 会退化成空上下文，等于零开销。
"""

from contextlib import nullcontext
from typing import (
    Any,
    Optional,
)

from langfuse import (
    Langfuse,
    propagate_attributes,
)
from langfuse.langchain import CallbackHandler

from app.core.config import settings
from app.core.logging import (
    get_context,
    logger,
)

# Langfuse 对 session_id / user_id 的要求：US-ASCII，且不超过 200 字符
_TRACE_ATTRIBUTE_MAX_LENGTH = 200

# 每条 trace 都带的标签，方便在界面上按项目筛
_BASE_TAGS = ("fly-agent",)

_client: Optional[Langfuse] = None


def langfuse_init() -> None:
    """初始化 Langfuse 客户端并做一次鉴权探测。

    鉴权失败只记录告警、不向上抛：追踪是旁路能力，
    它连不上不应该让整个服务起不来。
    """
    global _client

    if not settings.LANGFUSE_TRACING_ENABLED:
        logger.debug("langfuse_tracing_disabled")
        return

    _client = Langfuse(
        tracing_enabled=settings.LANGFUSE_TRACING_ENABLED,
        public_key=settings.LANGFUSE_PUBLIC_KEY,
        secret_key=settings.LANGFUSE_SECRET_KEY,
        host=settings.LANGFUSE_HOST,
        environment=settings.ENVIRONMENT.value,
        debug=settings.DEBUG,
    )
    client = _client

    try:
        if client.auth_check():
            logger.debug("langfuse_auth_success", host=settings.LANGFUSE_HOST)
        else:
            logger.warning("langfuse_auth_failure", host=settings.LANGFUSE_HOST)
    except Exception:
        logger.exception("langfuse_auth_check_failed", host=settings.LANGFUSE_HOST)


def get_client() -> Optional[Langfuse]:
    """取当前 Langfuse 客户端。

    Returns:
        Optional[Langfuse]: 追踪未启用时为 None
    """
    return _client


def get_langfuse_callback_handler() -> CallbackHandler:
    """创建用于追踪 LangChain / LangGraph 调用的回调处理器。

    Returns:
        CallbackHandler: 已配置好的回调处理器
    """
    return CallbackHandler()


def _clean_attribute(value: Any, field: str) -> Optional[str]:
    """校验并裁剪 trace 级属性，非法值直接丢弃。

    中文昵称、超长会话 ID 这类值会让 Langfuse 静默丢字段，
    这里提前拦掉并留一条日志，省得查不到数据时没有线索。

    Args:
        value: 待校验的原始值
        field: 字段名，仅用于日志

    Returns:
        Optional[str]: 合法值；None 表示不可用
    """
    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    if not text.isascii():
        logger.warning("langfuse_attribute_dropped", field=field, reason="non_ascii")
        return None

    return text[:_TRACE_ATTRIBUTE_MAX_LENGTH]


def trace_attributes(
    *,
    session_id: Optional[str] = None,
    user_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    metadata: Optional[dict[str, str]] = None,
) -> Any:
    """把会话、用户与标签挂到当前 trace，并向下透传给所有子 span。

    未传 session_id / user_id 时，自动回落到 :mod:`app.core.logging` 里
    请求上下文已绑定的值——中间件绑过一次，业务层就不必层层透传。

    用法（尽量早调用，并包住整段要追踪的逻辑）::

        with trace_attributes(session_id=session.id, user_id=user.id, tags=["chat"]):
            await graph.ainvoke(...)

    Args:
        session_id: 会话 ID，Langfuse 里按它把多轮对话串成一条会话
        user_id: 用户 ID，用于按用户统计成本与延迟
        tags: 额外标签，会与环境名、项目名一起写入
        metadata: 附加在 trace 上的键值，仅支持字符串值

    Returns:
        Any: 上下文管理器；追踪未启用时返回空上下文
    """
    if _client is None:
        return nullcontext()

    context = get_context()
    cleaned_session = _clean_attribute(session_id or context.get("session_id"), "session_id")
    cleaned_user = _clean_attribute(user_id or context.get("user_id"), "user_id")

    merged_tags = [*_BASE_TAGS, settings.ENVIRONMENT.value, *(tags or [])]
    merged_metadata: dict[str, str] = {"app_env": settings.ENVIRONMENT.value}
    if metadata:
        merged_metadata.update(metadata)

    return propagate_attributes(
        session_id=cleaned_session,
        user_id=cleaned_user,
        tags=merged_tags,
        metadata=merged_metadata,
    )


def flush() -> None:
    """强制把缓冲区里的追踪数据上报。

    服务进程常驻、后台线程会自己刷；但命令行脚本跑完就退，
    不显式 flush 会丢掉最后一批 span。
    """
    if _client is None:
        return

    try:
        _client.flush()
        logger.debug("langfuse_flushed")
    except Exception:
        logger.exception("langfuse_flush_failed")


def trace_url(trace_id: str) -> Optional[str]:
    """拼出某条 trace 在 Langfuse 界面里的地址。

    Args:
        trace_id: Langfuse 的 trace ID

    Returns:
        Optional[str]: 直链地址；取不到时返回 None
    """
    if _client is None:
        return None

    try:
        return _client.get_trace_url(trace_id=trace_id)
    except Exception:
        logger.exception("langfuse_trace_url_failed", trace_id=trace_id)
        return None


langfuse_callback_handler = get_langfuse_callback_handler()
