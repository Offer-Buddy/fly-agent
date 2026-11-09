"""模型注册表：启动时把配置里的模型全部实例化好备用。

模型清单完全来自配置（``DEFAULT_LLM_MODEL`` + ``LLM_FALLBACK_MODELS``），
代码里不写死任何模型名，换供应商只是改环境变量。
"""

from typing import (
    Any,
    Dict,
    List,
)

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from pydantic import SecretStr

from app.core.config import settings
from app.core.logging import logger

_API_KEY = SecretStr(settings.OPENAI_API_KEY)

# 这里每个模型都是推理模型，一旦设置了 reasoning，API 会拒绝传统的采样参数
# （top_p、presence_penalty、frequency_penalty）并返回 400。
# 需要调质量请改 reasoning.effort。


def _is_openai_model(model: str) -> bool:
    """判断某个模型名是否走 OpenAI 协议。

    与 ``init_chat_model`` 的推断保持一致：裸 ``gpt-*``（无 ``provider:`` 前缀）
    等价于显式写 ``openai:`` 前缀。此外只要配置了 ``OPENAI_BASE_URL``，
    任何裸模型名都会被当作 OpenAI 兼容端点处理。

    Args:
        model: 模型名，如 ``gpt-5.6-luna``、``openai:gpt-4o``、``deepseek-v4-flash``

    Returns:
        bool: 是否按 OpenAI 协议调用
    """
    if model.startswith("openai:"):
        return True
    if ":" in model:
        return False
    if model.startswith("gpt-"):
        return True
    return bool(settings.OPENAI_BASE_URL)


def _build_llm(model: str, **kwargs: Any) -> BaseChatModel:
    """通过 ``init_chat_model`` 构造一个对话模型。

    ``model`` 支持两种写法：裸模型名（由 ``init_chat_model`` 自行推断供应商，
    例如 ``gpt-5.6-luna`` → OpenAI，这样注册表里的旧配置不用改），
    或显式的 ``provider:model``（例如 ``anthropic:claude-opus-4-6``、
    ``ollama:llama3``）。非 OpenAI 供应商需要安装对应的 LangChain 集成包
    （见 pyproject.toml 的 ``anthropic`` / ``ollama`` extra），
    并从各自的环境变量读取 key（如 ``ANTHROPIC_API_KEY``）。
    OpenAI 兼容的第三方端点（``openai:`` 模型 + 自定义 ``base_url``）
    必须显式传它自己的 ``api_key``。

    Args:
        model: 模型名
        **kwargs: 透传给 ``init_chat_model`` 的参数

    Returns:
        BaseChatModel: 构造好的模型实例

    Raises:
        ValueError: 传了自定义 base_url 却没传对应的 api_key
    """
    if _is_openai_model(model):
        # 不显式传 key 时，ChatOpenAI 会把 OPENAI_API_KEY 发到自定义端点上。
        if kwargs.keys() & {"base_url", "openai_api_base"} and not kwargs.keys() & {"api_key", "openai_api_key"}:
            raise ValueError(f"'{model}' sets a custom base_url but no api_key; pass that endpoint's own api_key")
        kwargs.setdefault("api_key", _API_KEY)
        # 把所有走 OpenAI 协议的语言模型调用都指向配置的兼容端点
        if settings.OPENAI_BASE_URL:
            kwargs.setdefault("base_url", settings.OPENAI_BASE_URL)
            # ``init_chat_model`` 会根据模型名去推断供应商（如
            # "deepseek-v4-flash" → langchain_deepseek）。这里强制钉死为
            # openai 协议，让任何兼容模型名都能直接使用。
            if ":" not in model:
                kwargs["model_provider"] = "openai"
    return init_chat_model(model, **kwargs)


def _build_model_configs() -> List[Dict[str, Any]]:
    """从配置构建模型列表：默认模型在前，其余为后备。

    ``DEFAULT_LLM_MODEL`` 是环形降级的起点，也是智能体实际对话使用的模型；
    ``LLM_FALLBACK_MODELS`` 往后追加。全部来自环境变量，换供应商只改配置。

    Returns:
        List[Dict[str, Any]]: 每个元素含 name / model / kwargs
    """
    names: List[str] = [settings.DEFAULT_LLM_MODEL]
    for name in settings.LLM_FALLBACK_MODELS:
        if name and name not in names:
            names.append(name)

    configs: List[Dict[str, Any]] = []
    for name in names:
        kwargs: Dict[str, Any] = {}
        bare_name = name.split(":")[-1]
        if _is_openai_model(name) and bare_name.startswith("gpt-"):
            # OpenAI 推理模型不收 max_tokens，改用 max_completion_tokens，
            # 并用 reasoning.effort 控制思考深度。
            kwargs["max_completion_tokens"] = settings.MAX_TOKENS
            kwargs["reasoning"] = {"effort": "low"}
        else:
            kwargs["max_tokens"] = settings.MAX_TOKENS
        configs.append({"name": name, "model": name, "kwargs": kwargs})
    return configs


class LLMRegistry:
    """可用模型注册表，实例在类加载时就已建好。

    维护一份模型配置列表，并支持按名字取实例（可带参数覆盖）。
    """

    # 按优先级排序：下标 0 是默认模型，也是环形降级的起点。
    # 条目来自环境变量（先 DEFAULT_LLM_MODEL，再 LLM_FALLBACK_MODELS），
    # 所以换供应商或换模型只是改配置，不用改代码。
    LLM_CONFIGS: List[Dict[str, Any]] = _build_model_configs()

    LLMS: List[Dict[str, Any]] = [
        {"name": config["name"], "llm": _build_llm(config["model"], **config.get("kwargs", {}))}
        for config in LLM_CONFIGS
    ]

    @classmethod
    def get(cls, model_name: str, **kwargs) -> BaseChatModel:
        """按名字取模型，可传参数覆盖。

        传了 kwargs 时会新建一个实例并把覆盖参数应用上去，
        不影响注册表里共享的那份。

        Args:
            model_name: 模型名
            **kwargs: 覆盖默认配置的参数

        Returns:
            BaseChatModel: 模型实例

        Raises:
            ValueError: 模型名不在注册表中
        """
        config = next((c for c in cls.LLM_CONFIGS if c["name"] == model_name), None)

        if not config:
            available = ", ".join(c["name"] for c in cls.LLM_CONFIGS)
            raise ValueError(f"model '{model_name}' not found in registry. available models: {available}")

        if kwargs:
            # 真实模型 id 从 config 取，而不是用注册表名，
            # 这样即使两者将来不一致，也不会把未知 id 发给 API。
            logger.debug(
                "creating_llm_with_custom_args",
                model_name=model_name,
                model=config["model"],
                custom_args=list(kwargs.keys()),
            )
            override_kwargs: Dict[str, Any] = dict(kwargs)
            bare_name = config["model"].split(":")[-1]
            if _is_openai_model(config["model"]) and bare_name.startswith("gpt-"):
                # 这里只补 token 上限，没带每条配置里的 reasoning；
                # 将来若需要覆盖推理模型的该参数，加在这里即可。
                override_kwargs.setdefault("max_completion_tokens", settings.MAX_TOKENS)
            return _build_llm(config["model"], **override_kwargs)

        logger.debug("using_default_llm_instance", model_name=model_name)
        return next(entry["llm"] for entry in cls.LLMS if entry["name"] == model_name)

    @classmethod
    def get_all_names(cls) -> List[str]:
        """按顺序返回全部已注册的模型名。

        Returns:
            List[str]: 模型名列表
        """
        return [config["name"] for config in cls.LLM_CONFIGS]

    @classmethod
    def get_model_at_index(cls, index: int) -> Dict[str, Any]:
        """取指定下标的模型条目，越界时回绕到 0。

        Args:
            index: LLMS 中的下标

        Returns:
            Dict[str, Any]: 模型条目
        """
        if 0 <= index < len(cls.LLMS):
            return cls.LLMS[index]
        return cls.LLMS[0]
