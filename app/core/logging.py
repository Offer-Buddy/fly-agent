"""应用日志配置与初始化。

本模块基于 structlog 提供结构化日志能力，按环境切换**每个出口**的渲染方式：

- 控制台：开发环境输出便于阅读的彩色文本，预发 / 生产输出 JSON
- 文件：始终输出单行 JSON（JSONL），便于采集与 jq / Loki 检索

关键结构：structlog 处理器链只负责**组装字段**，渲染交给
``ProcessorFormatter``，由每个 handler 自己决定长什么样。这样同一份字段
能同时满足两个出口——控制台要看得懂，文件要查得到。

相对的，把渲染器挂在处理器链末尾（早期写法）会让字典在到达 handler 之前
就变成字符串，文件里只剩 ``message`` 一个字段，``duration_ms`` 这类数值
再也查不出来。
"""

import logging
import sys
from contextvars import ContextVar
from datetime import (
    datetime,
    timedelta,
)
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

# 日志文件保留天数：跨天切换文件时顺手清理更早的历史文件
LOG_BACKUP_DAYS = 30

# UTF-8 BOM 的三个字节。Windows 侧的查看器靠它认出「这是 UTF-8」
UTF8_BOM = b"\xef\xbb\xbf"

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


def add_environment(logger: Any, method_name: str, event_dict: Dict[str, Any]) -> Dict[str, Any]:
    """给每条日志附加环境标识，便于多环境日志混在一起时区分。

    Args:
        logger: 日志器实例
        method_name: 日志方法名
        event_dict: 待处理的事件字典

    Returns:
        Dict[str, Any]: 附加了 environment 的事件字典
    """
    event_dict["environment"] = settings.ENVIRONMENT.value
    return event_dict


def get_log_file_path() -> Path:
    """按环境与日期生成当天的日志文件路径。

    Returns:
        Path: 日志文件路径
    """
    env_prefix = settings.ENVIRONMENT.value
    return settings.LOG_DIR / f"{env_prefix}-{datetime.now().strftime('%Y-%m-%d')}.jsonl"


class JsonlFileHandler(logging.Handler):
    """把已渲染的单行文本追加写入按天切分的 JSONL 文件。

    渲染由挂在实例上的 ``ProcessorFormatter`` 完成，本类只负责三件事：
    「按日期挑文件 + 追加一行 + 清理过期文件」。

    文件名里带日期，所以跨过午夜后必须重新取路径——否则会一直往启动
    当天的文件里写，第二天的日志全进了昨天的文件。
    """

    def __init__(self, backup_days: int = LOG_BACKUP_DAYS):
        """初始化 JSONL 文件处理器。

        Args:
            backup_days: 历史日志保留天数
        """
        super().__init__()
        self.backup_days = backup_days
        self._current_date: Optional[str] = None
        self._file_path: Optional[Path] = None

    def _current_file(self) -> Path:
        """返回当天应写入的文件，跨天时切换并清理过期文件。

        Returns:
            Path: 当天日志文件路径
        """
        today = datetime.now().strftime("%Y-%m-%d")
        if today == self._current_date and self._file_path is not None:
            return self._file_path

        self._current_date = today
        self._file_path = get_log_file_path()
        self._prune_expired()
        self._ensure_bom(self._file_path)
        return self._file_path

    def _ensure_bom(self, path: Path) -> None:
        """确保日志文件首字节是 UTF-8 BOM，仅在文件为空时写一次。

        Windows 上的查看器（记事本、PowerShell 5.1 的 ``Get-Content``、Excel）
        默认按系统 ANSI 代码页解码，简体中文环境即 cp936。无 BOM 的 UTF-8 会被
        当成 GBK，中文立刻变成「涓枃」这类乱码。补上 BOM 后这些工具都能正确
        识别；BOM 只占文件第一个字节，不影响逐行 JSON 解析。

        Args:
            path: 当天的日志文件路径
        """
        if not settings.LOG_FILE_BOM:
            return

        try:
            if path.exists() and path.stat().st_size > 0:
                # 已有内容（进程重启、跨天回写）说明 BOM 早就写过了，
                # 再写一次会插到行中间，反而破坏 JSONL
                return
            with open(path, "ab") as f:
                f.write(UTF8_BOM)
        except OSError:
            # 补 BOM 只影响可读性，写日志本身不能被它拖住
            return

    def _prune_expired(self) -> None:
        """删除超过保留天数的历史日志，避免日志目录无限增长。"""
        cutoff = datetime.now() - timedelta(days=self.backup_days)
        for path in settings.LOG_DIR.glob(f"{settings.ENVIRONMENT.value}-*.jsonl"):
            try:
                if datetime.fromtimestamp(path.stat().st_mtime) < cutoff:
                    path.unlink()
            except OSError:
                # 清理失败不影响写日志，等下次跨天再试
                continue

    @override
    def emit(self, record: logging.LogRecord) -> None:
        """把一条日志记录写成一行文本。

        Args:
            record: 标准库日志记录，``format`` 之后即是完整的一行 JSON
        """
        try:
            line = self.format(record)
            with open(self._current_file(), "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            self.handleError(record)


def get_structlog_processors(include_file_info: bool = True) -> List[Any]:
    """组装 structlog 自身的处理器链（应用代码打的日志走这条）。

    Args:
        include_file_info: 是否附带文件 / 函数 / 行号等调用点信息

    Returns:
        List[Any]: structlog 处理器列表
    """
    processors: List[Any] = [
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        # 异常压成字符串，必须早于渲染
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

    processors.append(add_environment)
    return processors


def get_foreign_processors() -> List[Any]:
    """组装第三方库日志的补字段链（``foreign_pre_chain``）。

    标准库 ``LogRecord`` 已经自带级别、logger 名和调用点，所以这里不再放
    ``filter_by_level`` / ``add_logger_name`` / ``CallsiteParameterAdder``，
    只补 structlog 链才有的那几个字段。

    Returns:
        List[Any]: 供 ``ProcessorFormatter.foreign_pre_chain`` 使用的处理器列表
    """
    return [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
        add_context_to_event_dict,
        add_request_id_to_event_dict,
        add_environment,
    ]


def build_formatter(renderer: Any) -> structlog.stdlib.ProcessorFormatter:
    """用指定渲染器构造一个 ProcessorFormatter。

    渲染放在 formatter 里，而不是放在 structlog 处理器链末尾。这是本模块
    最关键的一处：链只组装字段，由每个 handler 自己决定输出成彩色文本
    还是 JSON，两个出口互不将就。

    Args:
        renderer: 渲染器实例（ConsoleRenderer 或 JSONRenderer）

    Returns:
        structlog.stdlib.ProcessorFormatter: 可直接挂到 handler 上的格式化器
    """
    return structlog.stdlib.ProcessorFormatter(
        # 第三方库（httpx、sqlalchemy 等）的日志也过一遍，字段才统一
        foreign_pre_chain=get_foreign_processors(),
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )


def setup_logging() -> None:
    """配置 structlog 与标准库 logging，建好控制台与文件两个出口。

    - 级别读 ``settings.LOG_LEVEL``（环境默认值 + 环境变量共同决定）
    - 控制台：``LOG_FORMAT=console`` 时彩色文本，否则 JSON
    - 文件：始终单行 JSON，按天切分，保留 ``LOG_BACKUP_DAYS`` 天
    """
    log_level = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    # 文件出口：文件永远是 JSON，不受 LOG_FORMAT 影响
    file_handler = JsonlFileHandler()
    file_handler.setLevel(log_level)
    file_handler.setFormatter(build_formatter(structlog.processors.JSONRenderer(ensure_ascii=False)))

    # 控制台出口：开发看彩色行，生产看 JSON
    console_renderer: Any = (
        structlog.dev.ConsoleRenderer()
        if settings.LOG_FORMAT == "console"
        else structlog.processors.JSONRenderer(ensure_ascii=False)
    )
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_handler.setFormatter(build_formatter(console_renderer))

    # structlog 自己的链：只组装字段，最后一步交给 formatter
    structlog.configure(
        processors=[
            *get_structlog_processors(
                # 只在开发与测试环境附带详细调用点信息
                include_file_info=settings.ENVIRONMENT in [Environment.DEVELOPMENT, Environment.TEST]
            ),
            # wrap_for_formatter 必须放最后，它只负责把字段打包交出去
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    root = logging.getLogger()
    # 先清空再挂：避免热重载时留下一份旧 handler，导致每条日志重复两遍
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    root.setLevel(log_level)


# 模块导入即完成日志初始化（全项目最早执行的初始化之一）
setup_logging()

# 全局日志器实例
logger = structlog.get_logger()
logger.info(
    "logging_initialized",
    environment=settings.ENVIRONMENT.value,
    log_level=settings.LOG_LEVEL,
    log_format=settings.LOG_FORMAT,
    log_backup_days=LOG_BACKUP_DAYS,
    debug=settings.DEBUG,
)
