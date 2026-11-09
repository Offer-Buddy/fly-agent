"""应用日志配置与初始化。

本模块基于 structlog 提供结构化日志能力，按环境切换输出渲染方式：
开发环境输出便于阅读的彩色控制台文本，预发 / 生产环境输出 JSON 便于采集。
日志同时写入两个目标——标准输出与按天切分的 JSONL 文件。
"""

import json
import logging
import sys
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import (
    Any,
    Dict,
    List,
    Optional,
    override,
)

import structlog
from asgi_correlation_id import correlation_id

from app.core.config import (
    Environment,
    settings,
)

# 确保日志目录存在（首次导入时创建，失败会直接抛出，属于预期行为）
settings.LOG_DIR.mkdir(parents=True, exist_ok=True)

# 存放请求级上下文（user_id、session_id 等）的上下文变量
_request_context: ContextVar[Optional[Dict[str, Any]]] = ContextVar("request_context", default=None)


def bind_context(**kwargs: Any) -> None:
    """把上下文变量绑定到当前请求。

    Args:
        **kwargs: 要绑定到日志上下文的键值对
    """
    current = _request_context.get() or {}
    _request_context.set({**current, **kwargs})


def clear_context() -> None:
    """清空当前请求的全部上下文变量。"""
    _request_context.set(None)


def get_context() -> Dict[str, Any]:
    """读取当前日志上下文。

    Returns:
        Dict[str, Any]: 当前上下文字典
    """
    return _request_context.get() or {}


def add_context_to_event_dict(logger: Any, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """把上下文变量合并进日志事件字典。

    这是一个 structlog 处理器：每次写日志时，把当前请求绑定的上下文
    一并附加到事件上，避免每个调用点重复传参。

    Args:
        logger: 日志器实例
        method_name: 日志方法名
        event_dict: 待处理的事件字典

    Returns:
        Dict[str, Any]: 附加了上下文的事件字典
    """
    context = get_context()
    if context:
        event_dict.update(context)
    return event_dict


def add_request_id_to_event_dict(logger: Any, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """把当前 request_id（来自 asgi-correlation-id）附加到每条日志。

    Args:
        logger: 日志器实例
        method_name: 日志方法名
        event_dict: 待处理的事件字典

    Returns:
        Dict[str, Any]: 附加了 request_id 的事件字典
    """
    request_id = correlation_id.get()
    if request_id:
        event_dict["request_id"] = request_id
    return event_dict


def get_log_file_path() -> Path:
    """按环境与日期生成当天的日志文件路径。

    Returns:
        Path: 日志文件路径
    """
    env_prefix = settings.ENVIRONMENT.value
    return settings.LOG_DIR / f"{env_prefix}-{datetime.now().strftime('%Y-%m-%d')}.jsonl"


class JsonlFileHandler(logging.Handler):
    """把日志按行追加写入 JSONL 文件的标准 Handler。"""

    def __init__(self, file_path: Path):
        """初始化 JSONL 文件处理器。

        Args:
            file_path: 日志文件路径，条目以追加方式写入
        """
        super().__init__()
        self.file_path = file_path

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """把一条日志记录写入 JSONL 文件。"""
        try:
            log_entry = {
                "timestamp": datetime.fromtimestamp(record.created).isoformat(),
                "level": record.levelname,
                "message": record.getMessage(),
                "module": record.module,
                "function": record.funcName,
                "filename": record.pathname,
                "line": record.lineno,
                "environment": settings.ENVIRONMENT.value,
            }
            extra = getattr(record, "extra", None)
            if isinstance(extra, dict):
                log_entry.update(extra)

            with open(self.file_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception:
            self.handleError(record)

    @override
    def close(self) -> None:
        """关闭处理器。"""
        super().close()


def get_structlog_processors(include_file_info: bool = True) -> List[Any]:
    """按配置组装 structlog 处理器链。

    Args:
        include_file_info: 是否在日志中附带文件 / 函数 / 行号等调用点信息

    Returns:
        List[Any]: structlog 处理器列表
    """
    # 两种输出共用的处理器
    processors = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        # 把上下文变量（user_id、session_id 等）附加到每条日志
        add_context_to_event_dict,
        # 把 asgi-correlation-id 的 request_id 附加到每条日志
        add_request_id_to_event_dict,
    ]

    # 需要调用点信息时再加一层（生产环境关掉，减少开销）
    if include_file_info:
        processors.append(
            structlog.processors.CallsiteParameterAdder(
                {
                    structlog.processors.CallsiteParameter.FILENAME,
                    structlog.processors.CallsiteParameter.FUNC_NAME,
                    structlog.processors.CallsiteParameter.LINENO,
                    structlog.processors.CallsiteParameter.MODULE,
                    structlog.processors.CallsiteParameter.PATHNAME,
                }
            )
        )

    # 附加环境标识，便于多环境日志混在一起时区分
    processors.append(lambda _, __, event_dict: {**event_dict, "environment": settings.ENVIRONMENT.value})

    return processors


def setup_logging() -> None:
    """按环境配置 structlog，并初始化标准库 logging 的处理器。

    开发环境：控制台彩色输出，可读性优先。
    预发 / 生产：结构化 JSON 输出，便于采集与检索。
    """
    # 日志级别跟随 DEBUG 开关
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO

    # 文件处理器：按天写 JSONL
    file_handler = JsonlFileHandler(get_log_file_path())
    file_handler.setLevel(log_level)

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)

    # 共用处理器链
    shared_processors = get_structlog_processors(
        # 只在开发与测试环境附带详细调用点信息
        include_file_info=settings.ENVIRONMENT in [Environment.DEVELOPMENT, Environment.TEST]
    )

    # 配置标准库 logging（structlog 最终通过它落地）
    logging.basicConfig(
        format="%(message)s",
        level=log_level,
        handlers=[file_handler, console_handler],
    )

    # 按输出格式选择渲染器
    if settings.LOG_FORMAT == "console":
        # 开发环境：彩色可读输出
        structlog.configure(
            processors=[
                *shared_processors,
                # ConsoleRenderer 负责着色与对齐
                structlog.dev.ConsoleRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # 生产环境：单行 JSON
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )


# 模块导入即完成日志初始化（全项目最早执行的初始化之一）
setup_logging()

# 全局日志器实例
logger = structlog.get_logger()
log_level_name = "DEBUG" if settings.DEBUG else "INFO"
logger.info(
    "logging_initialized",
    environment=settings.ENVIRONMENT.value,
    log_level=log_level_name,
    log_format=settings.LOG_FORMAT,
    debug=settings.DEBUG,
)
