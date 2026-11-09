"""应用配置管理。

所有运行时配置都以 ``pydantic-settings`` 的 ``BaseSettings`` 字段形式声明，
无论值来自操作系统环境变量还是 env 文件，都只在**导入时**解析并校验一次。

单个配置项的解析优先级（从高到低）：

1. 操作系统环境变量（``APP_ENV=staging uvicorn ...``）
2. 选中的 env 文件
3. 按环境区分的默认值（development / staging / production / test）
4. 模型字段上声明的默认值

env 文件仍然是手工挑选的，因为沿用历史布局：命中**第一个**存在的候选文件即
停止，而不是把所有文件合并：
``.env.<env>.local`` -> ``.env.<env>`` -> ``.env.local`` -> ``.env``。

注意：本模块的两行启动提示刻意用 ``print`` 而不是 ``structlog``，
因为 ``app.core.logging`` 反过来要 import 本模块的 ``settings``，
用日志会造成循环导入。
"""

import os
import tomllib
from enum import Enum
from functools import lru_cache
from importlib.metadata import PackageNotFoundError, metadata
from pathlib import Path
from typing import Annotated, Any, ClassVar, Literal

from dotenv import load_dotenv
from pydantic import AliasChoices, BeforeValidator, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# 包名，用于从安装元数据里取版本号与描述（与 pyproject.toml 单一来源）
PACKAGE_NAME = "fly-agent"

# 读不到任何元数据时的兜底描述
_FALLBACK_DESCRIPTION = "基于 FastAPI + LangGraph 的 AI 智能体服务"


# 已知的弱密钥 / 占位符，生产环境出现任何一个都直接拒绝启动
WEAK_JWT_SECRET_KEYS = {
    "",
    "changeme",
    "change_me",
    "change_me_to_a_random_32_plus_char_string",
    "supersecretkeythatshouldbechangedforproduction",
    "your-jwt-secret-key",
}


def _read_package_metadata() -> tuple[str, str]:
    """读出本项目的版本号与描述，``pyproject.toml`` 是唯一来源。

    查找顺序：
    1. 已安装发行包的元数据 —— 有人 ``pip install`` 过本项目时走这条；
    2. 直接读 ``pyproject.toml`` —— 本项目常态（源码即应用，``pyproject`` 没有
       build-system，``uv sync`` 不会把它装成一个发行包，元数据查不到）；
    3. 兜底值 —— 两者都拿不到时保证仍有可用字符串，不抛异常。

    这么做的意义：以前 ``VERSION`` 是手写配置，与 pyproject 的 ``version``
    各写一份，已经漂移成了 ``1.0.0`` / ``0.1.0``，现在只可能有一个值。

    Returns:
        tuple[str, str]: (版本号, 描述)
    """
    try:
        meta = metadata(PACKAGE_NAME)
        return meta["Version"], meta["Summary"] or _FALLBACK_DESCRIPTION
    except PackageNotFoundError:
        pass

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    try:
        project = tomllib.loads(pyproject.read_text(encoding="utf-8"))["project"]
        return str(project["version"]), str(project.get("description") or _FALLBACK_DESCRIPTION)
    except (OSError, KeyError, tomllib.TOMLDecodeError):
        return "0.0.0+unknown", _FALLBACK_DESCRIPTION


_PACKAGE_VERSION, _PACKAGE_DESCRIPTION = _read_package_metadata()

# 各接口的默认限流额度，可被 RATE_LIMIT_<ENDPOINT> 环境变量覆盖
_RATE_LIMIT_ENDPOINT_DEFAULTS: dict[str, list[str]] = {
    "chat": ["30 per minute"],
    "chat_stream": ["20 per minute"],
    "messages": ["50 per minute"],
    "register": ["10 per hour"],
    "login": ["20 per minute"],
    "root": ["10 per minute"],
    "health": ["20 per minute"],
}


class Environment(str, Enum):
    """应用运行环境。

    共四种：开发、预发、生产、测试。取值会出现在日志文件名、
    ``settings.ENVIRONMENT`` 判断以及按环境覆盖的默认值里。
    """

    DEVELOPMENT = "development"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


# 按环境区分的默认值，仅在「环境变量和 env 文件都没给值」时生效
# （具体逻辑见 ``Settings._apply_environment_defaults``）
_ENV_SPECIFIC_DEFAULTS: dict[Environment, dict[str, Any]] = {
    Environment.DEVELOPMENT: {
        "DEBUG": True,
        "LOG_LEVEL": "DEBUG",
        "LOG_FORMAT": "console",
        "RATE_LIMIT_DEFAULT": ["1000 per day", "200 per hour"],
    },
    Environment.STAGING: {
        "DEBUG": False,
        "LOG_LEVEL": "INFO",
        "RATE_LIMIT_DEFAULT": ["500 per day", "100 per hour"],
    },
    Environment.PRODUCTION: {
        "DEBUG": False,
        "LOG_LEVEL": "WARNING",
        "RATE_LIMIT_DEFAULT": ["200 per day", "50 per hour"],
    },
    Environment.TEST: {
        "DEBUG": True,
        "LOG_LEVEL": "DEBUG",
        "LOG_FORMAT": "console",
        "RATE_LIMIT_DEFAULT": ["1000 per day", "1000 per hour"],  # 测试环境放宽限流
    },
}


def _parse_environment(raw: str | None) -> Environment:
    """把 ``APP_ENV`` 的原始字符串映射成 ``Environment`` 枚举。

    兼容 ``dev`` / ``stage`` / ``prod`` 简写，且不区分大小写；空值（未设置）
    回落 ``development``。**无法识别的值直接报错**——``APP_ENV`` 会连带改变
    DEBUG、日志级别、日志格式和限流额度，拼错后静默当成 development 跑，
    排查方向会完全跑偏，所以宁可启动失败。

    Args:
        raw: 环境变量原始值，可能为 ``None``

    Returns:
        Environment: 对应的环境枚举

    Raises:
        ValueError: 取值不在认可范围内
    """
    text = (raw or "").strip().lower()
    match text:
        case "" | "development" | "dev":
            return Environment.DEVELOPMENT
        case "production" | "prod":
            return Environment.PRODUCTION
        case "staging" | "stage":
            return Environment.STAGING
        case "test":
            return Environment.TEST
        case _:
            raise ValueError(
                f"无法识别的 APP_ENV={raw!r}。可选值：development / staging / production / test"
                "（也接受 dev、stage、prod 简写）"
            )


def _bootstrap_environment() -> Environment:
    """决定「该加载哪个 env 文件」的环境。

    这里只看真实的操作系统环境变量——env 文件里的 ``APP_ENV`` 无权决定
    自己是否被加载（否则会出现先有鸡还是先有蛋的问题）。

    Returns:
        Environment: 用于挑选 env 文件的环境
    """
    return _parse_environment(os.getenv("APP_ENV"))


def _resolve_env_file(env: Environment) -> str | None:
    """按优先级返回第一个存在的 env 文件路径。

    基于项目根目录换算绝对路径，因此与启动时的工作目录无关；
    一个都不存在时返回 ``None``，此时全部走字段默认值。

    Args:
        env: 当前环境

    Returns:
        str | None: env 文件的绝对路径
    """
    base_dir = Path(__file__).resolve().parents[2]
    candidates = (
        f".env.{env.value}.local",
        f".env.{env.value}",
        ".env.local",
        ".env",
    )
    for name in candidates:
        candidate = base_dir / name
        if candidate.is_file():
            return str(candidate)
    return None


_ENVIRONMENT_FOR_ENV_FILE = _bootstrap_environment()
ENV_FILE = _resolve_env_file(_ENVIRONMENT_FOR_ENV_FILE)

if ENV_FILE:
    print(f"Loading environment: {_ENVIRONMENT_FOR_ENV_FILE.value}")
    # 保留 load_dotenv：有些第三方库（Langfuse、mem0 等）直接读 os.environ，
    # 不把文件值写进环境变量，它们就拿不到配置。
    load_dotenv(dotenv_path=ENV_FILE, override=False)
    print(f"Loaded environment from {ENV_FILE}")


def _env_lookup(key: str, default: str | None = None) -> str | None:
    """查一个配置值：先看操作系统环境变量，再看已加载的 env 文件。

    由于导入时已经把 env 文件写进 ``os.environ``，当前实现用 ``os.getenv``
    就够了；保留这个函数是为了给「动态键」（如 ``RATE_LIMIT_<ENDPOINT>``）
    留一个统一的、可全局检索的入口。

    Args:
        key: 变量名
        default: 未取到值（或值为空串）时的返回值

    Returns:
        str | None: 变量值或默认值
    """
    value = os.getenv(key)
    if value is None or value == "":
        return default
    return value


def get_environment() -> Environment:
    """获取当前运行环境。

    Returns:
        Environment: 当前环境（开发 / 预发 / 生产 / 测试）
    """
    return _parse_environment(_env_lookup("APP_ENV", "development"))


def _parse_csv(value: Any) -> list[str]:
    """把逗号分隔的字符串解析成字符串列表。

    已有的列表会逐个 strip 规整；``None`` 与空串返回空列表；
    外层引号会被去掉，因此 ``ALLOWED_ORIGINS="a,b"`` 这种常见写法依然可用。

    Args:
        value: 原始值，可能是字符串、列表或 ``None``

    Returns:
        list[str]: 解析后的列表
    """
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return []

    text = str(value).strip().strip("\"'")
    if not text:
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


# 「逗号分隔的字符串列表」注解类型。
#
# 为什么需要它：
#   1. pydantic-settings 默认把 list 类型当 JSON 解析，``a,b`` 会直接报错，
#      所以要先 NoDecode 关掉自动解码；
#   2. 关掉之后还得有人把字符串切开，BeforeValidator 在字段校验之前做这件事。
# 把这两步打包成一个类型的好处：新增列表型配置时只要标注 ``CSVList`` 即可，
# 不必再去某个 ``@field_validator`` 的字段名列表里登记（漏登记就是运行时
# JSON 解析报错，之前这里就靠人工同步，是个隐形的坑）。
CSVList = Annotated[list[str], NoDecode, BeforeValidator(_parse_csv)]


class Settings(BaseSettings):
    """带类型的应用配置模型。

    字段名刻意保持全大写：与环境变量名一一对应，同时让项目里所有
    ``settings.MAX_TOKENS`` 这类调用点无需改动。

    值在构造时集中校验一次，所以像 ``MAX_TOKENS=lots`` 这种手误会在启动
    瞬间以清晰的 pydantic 报错暴露，而不是等到某个请求处理到一半才抛
    ``ValueError``。
    """

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        # 环境变量名大小写敏感：写 project_name 不生效，必须 PROJECT_NAME
        case_sensitive=True,
        # env 文件里必然存在本模型不认识的键（如 RATE_LIMIT_CHAT），一律忽略
        extra="ignore",
        # 允许用字段名（而非仅别名）传参，便于 Settings(MAX_TOKENS=...)
        populate_by_name=True,
    )

    # ---- 应用基础信息 ----
    PROJECT_NAME: str = "FastAPI LangGraph Template"
    # 下面三个是 ClassVar 而非配置项，判断标准只有一条：
    #   「改了它，代码得同步改才能跑通」→ 它不是配置，是契约。
    # VERSION / DESCRIPTION 来自 pyproject 的包元数据（单一来源，不再手写两份）；
    # API_V1_STR 是路由契约，改了所有客户端都会断。
    # 声明成 ClassVar 后依旧可以 settings.VERSION 这样读，但不会再出现在环境变量里
    # （写进 .env 会被 extra="ignore" 静默忽略，所以 .env.example 里已删掉）。
    VERSION: ClassVar[str] = _PACKAGE_VERSION
    DESCRIPTION: ClassVar[str] = _PACKAGE_DESCRIPTION
    API_V1_STR: ClassVar[str] = "/api/v1"
    DEBUG: bool = False
    ENVIRONMENT: Environment = Field(
        default_factory=get_environment,
        # 环境变量沿用历史名 APP_ENV；同时保留字段名，方便代码里
        # 用 Settings(ENVIRONMENT="prod") 做局部覆盖。
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )

    # ---- 跨域 ----
    # 逗号分隔；留空等价于 ["*"]（见下面的 after 校验器）
    ALLOWED_ORIGINS: CSVList = ["*"]

    # ---- 可观测性：Langfuse ----
    LANGFUSE_TRACING_ENABLED: bool = True
    LANGFUSE_PUBLIC_KEY: str = ""
    LANGFUSE_SECRET_KEY: str = ""
    LANGFUSE_HOST: str = "https://cloud.langfuse.com"

    # ---- 大模型 ----
    OPENAI_API_KEY: str = ""
    # 可选的 OpenAI 兼容端点（DeepSeek、DashScope、Atlas Cloud、vLLM 等）。
    # 留空表示走官方 OpenAI API。
    OPENAI_BASE_URL: str = ""
    DEFAULT_LLM_MODEL: str = "gpt-5.6-luna"
    # 备用模型链，逗号分隔；主模型失败后按顺序轮转重试
    LLM_FALLBACK_MODELS: CSVList = []
    SESSION_NAMING_ENABLED: bool = True
    DEFAULT_LLM_TEMPERATURE: float = 0.2
    MAX_TOKENS: int = 2000
    MAX_LLM_CALL_RETRIES: int = 3
    LLM_TOTAL_TIMEOUT: int = 60

    # ---- 长期记忆（mem0）----
    LONG_TERM_MEMORY_MODEL: str = "gpt-5-nano"
    LONG_TERM_MEMORY_EMBEDDER_MODEL: str = "text-embedding-3-small"
    # 向量模型可以挂在和对话模型不同的端点上（例如 DeepSeek 没有 embeddings 接口）。
    # 留空则复用 OPENAI_BASE_URL / OPENAI_API_KEY。
    LONG_TERM_MEMORY_EMBEDDER_BASE_URL: str = ""
    LONG_TERM_MEMORY_EMBEDDER_API_KEY: str = ""
    # 必须与向量模型的真实维度一致，否则写入 pgvector 会失败
    LONG_TERM_MEMORY_EMBEDDING_DIMS: int = 1536
    LONG_TERM_MEMORY_COLLECTION_NAME: str = "longterm_memory"

    # ---- 鉴权 ----
    JWT_SECRET_KEY: str = ""
    # 同理属于契约：签发与校验两处代码都写死了 HS256，没有公钥加载逻辑，
    # 改成 RS256 不会"生效"，只会让所有令牌失效，故不暴露成配置项。
    JWT_ALGORITHM: ClassVar[str] = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_DAYS: int = 30

    # ---- 日志 ----
    LOG_DIR: Path = Path("logs")
    # 用 Literal 而不是 str：拼错（如 INFOO）在启动瞬间报错，
    # 而不是等到 logging 配置读取时才炸
    LOG_LEVEL: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    LOG_FORMAT: Literal["json", "console"] = "json"

    # ---- 性能剖析（仅 DEBUG 生效）----
    PROFILING_DIR: Path = Path("/tmp/fastapi_profiles")
    PROFILING_THRESHOLD_SECONDS: float = 2.0

    # ---- PostgreSQL ----
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "food_order_db"
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_POOL_SIZE: int = 20
    POSTGRES_MAX_OVERFLOW: int = 10
    # 这不是配置项，而是与 LangGraph 检查点存储的固定契约，故用 ClassVar
    CHECKPOINT_TABLES: ClassVar[list[str]] = ["checkpoint_blobs", "checkpoint_writes", "checkpoints"]

    # ---- Redis 缓存（可选：只要填了 host 就启用）----
    REDIS_HOST: str = ""
    REDIS_PORT: int = 6379
    REDIS_DB: int = 1
    REDIS_PASSWORD: str = ""
    REDIS_MAX_CONNECTIONS: int = 20
    CACHE_TTL_SECONDS: int = 60

    # ---- 限流 ----
    RATE_LIMIT_DEFAULT: CSVList = ["200 per day", "50 per hour"]
    # 由 ``_assemble_rate_limits`` 从 RATE_LIMIT_<ENDPOINT> 环境变量汇总而来
    RATE_LIMIT_ENDPOINTS: dict[str, list[str]] = Field(default_factory=dict)

    # ---- LLM 评测 ----
    EVALUATION_LLM: str = "gpt-5"
    EVALUATION_BASE_URL: str = "https://api.openai.com/v1"
    EVALUATION_API_KEY: str = ""
    EVALUATION_SLEEP_TIME: int = 10

    @field_validator("ENVIRONMENT", mode="before")
    @classmethod
    def _normalise_environment(cls, value: Any) -> Any:
        """兼容 ``prod`` / ``stage`` 简写与任意大小写。"""
        if isinstance(value, str):
            return _parse_environment(value)
        return value

    @field_validator("ALLOWED_ORIGINS", mode="after")
    @classmethod
    def _default_allowed_origins(cls, value: list[str]) -> list[str]:
        """留空等价于「放行全部来源」（沿用历史行为）。

        逗号切分已经由 ``CSVList`` 注解统一处理，这里只剩这一条与具体字段
        绑定的特殊语义，所以是个 after 校验器而不是 before。
        """
        return value or ["*"]

    @model_validator(mode="after")
    def _finalise(self) -> "Settings":
        """收尾处理：按环境补默认值、生成派生值、执行安全校验。

        在所有字段都从各自来源解析完毕后才执行一次，
        下面的调用顺序是刻意排好的，便于审阅。
        """
        explicit = frozenset(self.model_fields_set)
        self._apply_environment_defaults(explicit)
        self._assemble_rate_limits()
        self._fallback_evaluation_api_key(explicit)
        self._validate_jwt_secret_key()
        return self

    def _apply_environment_defaults(self, explicit: frozenset[str]) -> None:
        """为「没人显式设置过」的字段补上按环境区分的默认值。

        环境变量或 env 文件里写过的值永远优先；这里只负责兜底
        （例如开发环境自动开 DEBUG 日志、生产环境收紧限流）。

        Args:
            explicit: 被显式设置过的字段名集合
        """
        for key, value in _ENV_SPECIFIC_DEFAULTS.get(self.ENVIRONMENT, {}).items():
            if key in explicit:
                continue
            # 可变值要复制，避免多个实例共享同一个 list 对象
            setattr(self, key, list(value) if isinstance(value, list) else value)

    def _assemble_rate_limits(self) -> None:
        """从 ``RATE_LIMIT_<ENDPOINT>`` 环境变量汇总各接口限流额度。"""
        endpoints = {name: list(default) for name, default in _RATE_LIMIT_ENDPOINT_DEFAULTS.items()}
        for name in endpoints:
            parsed = _parse_csv(_env_lookup(f"RATE_LIMIT_{name.upper()}"))
            if parsed:
                endpoints[name] = parsed
        self.RATE_LIMIT_ENDPOINTS = endpoints

    def _fallback_evaluation_api_key(self, explicit: frozenset[str]) -> None:
        """没有单独的评测 key 时，复用 ``OPENAI_API_KEY``。

        Args:
            explicit: 被显式设置过的字段名集合
        """
        if "EVALUATION_API_KEY" not in explicit:
            self.EVALUATION_API_KEY = self.OPENAI_API_KEY

    def _validate_jwt_secret_key(self) -> None:
        """拒绝空的、过短的或占位符形式的 JWT 密钥（测试环境豁免）。

        这里刻意抛 ``RuntimeError`` 而不是 ``ValueError``：pydantic 会把
        ``ValueError`` 包装成 ``ValidationError``，而后者的 ``input_value``
        会回显整份配置字典——里面有 API Key，等于把密钥写进日志。

        Raises:
            RuntimeError: 密钥长度不足 32 位，或命中弱密钥黑名单
        """
        if self.ENVIRONMENT == Environment.TEST:
            return

        secret = self.JWT_SECRET_KEY.strip()
        if len(secret) < 32:
            raise RuntimeError("JWT_SECRET_KEY must be at least 32 characters long")

        if secret.lower() in WEAK_JWT_SECRET_KEYS:
            raise RuntimeError("JWT_SECRET_KEY must be a strong random value, not a placeholder or default")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级配置单例。

    带缓存，重复调用零成本；测试里改了环境变量后可调用
    ``get_settings.cache_clear()`` 强制重建。

    Returns:
        Settings: 配置实例
    """
    return Settings()


# 全局配置实例：业务代码统一从这里取，不要再直接读环境变量
settings = get_settings()
