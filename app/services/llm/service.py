"""LLM 调用服务：重试、环形降级、可选结构化输出。

对外只暴露 ``call()``、``get_llm()``、``bind_tools()`` 三个方法，
内部把「同一个模型重试 N 次 → 换下一个模型 → 全部失败抛错」这套逻辑收敛在
``_fallback_loop`` 里，避免散落在业务代码中。
"""

import asyncio
import logging
from typing import (
    Any,
    Callable,
    List,
    Optional,
    Type,
    TypeVar,
    Union,
    overload,
)

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import BaseMessage
from openai import (
    APIError,
    APITimeoutError,
    RateLimitError,
)
from pydantic import BaseModel
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.logging import logger
from app.services.llm.registry import LLMRegistry

T = TypeVar("T", bound=BaseModel)


class LLMService:
    """LLM 调用服务，负责重试与环形降级。

    两条执行路径，互不干扰：

    - **默认路径**（不传 ``model_name`` / ``response_format`` / ``model_kwargs``）：
      走 ``self._llm``，也就是已经绑定过工具的智能体模型。降级切换时会
      重建 ``self._llm``，因此工具绑定得以保留。

    - **一次性路径**（传了任意一个覆盖参数）：每次调用现建一个局部的
      ``Runnable``，完全不碰 ``self._llm``，所以并发下的默认路径调用不受影响。
    """

    def __init__(self):
        """用配置里的默认模型初始化服务。"""
        self._llm: Any = None  # bind_tools 之前是 BaseChatModel，之后是 Runnable
        self._current_model_index: int = 0
        self._bound_tools: List = []

        all_names = LLMRegistry.get_all_names()
        try:
            self._current_model_index = all_names.index(settings.DEFAULT_LLM_MODEL)
            self._llm = LLMRegistry.get(settings.DEFAULT_LLM_MODEL)
            logger.info(
                "llm_service_initialized",
                default_model=settings.DEFAULT_LLM_MODEL,
                model_index=self._current_model_index,
                total_models=len(all_names),
                environment=settings.ENVIRONMENT.value,
            )
        except Exception as e:
            # 配置的默认模型不在注册表里：退到第一个可用模型，别让服务起不来
            self._current_model_index = 0
            self._llm = LLMRegistry.LLMS[0]["llm"]
            logger.warning(
                "default_model_not_found_using_first",
                requested=settings.DEFAULT_LLM_MODEL,
                using=all_names[0] if all_names else "none",
                error=str(e),
            )

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------

    @overload
    async def call(
        self,
        messages: LanguageModelInput,
        model_name: Optional[str] = ...,
        response_format: None = ...,
        **model_kwargs: Any,
    ) -> BaseMessage: ...

    @overload
    async def call(
        self,
        messages: LanguageModelInput,
        model_name: Optional[str] = ...,
        *,
        response_format: Type[T],
        **model_kwargs: Any,
    ) -> T: ...

    async def call(
        self,
        messages: LanguageModelInput,
        model_name: Optional[str] = None,
        response_format: Optional[Type[BaseModel]] = None,
        **model_kwargs: Any,
    ) -> Union[BaseMessage, BaseModel]:
        """调用 LLM，带重试与环形降级。

        Args:
            messages: 要发送的对话消息
            model_name: 指定模型；``None`` 表示用当前默认模型
            response_format: 结构化输出用的 pydantic 模型。传了就会链上
                ``.with_structured_output(schema)``，返回该模型的实例而不是
                原始 ``BaseMessage``
            **model_kwargs: 走一次性路径时透传给 ``LLMRegistry.get`` 的额外参数
                （例如 ``temperature``、``max_tokens``、``reasoning``）

        Returns:
            ``response_format`` 为 ``None`` 时返回 ``BaseMessage``，
            否则返回 ``response_format`` 的校验实例

        Raises:
            RuntimeError: 所有模型重试完仍失败，或超出总超时预算
        """
        try:
            return await asyncio.wait_for(
                self._call_with_fallback(messages, model_name, response_format, model_kwargs),
                timeout=settings.LLM_TOTAL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.exception(
                "llm_total_timeout_exceeded",
                timeout_seconds=settings.LLM_TOTAL_TIMEOUT,
            )
            raise RuntimeError(f"llm call timed out after {settings.LLM_TOTAL_TIMEOUT}s total budget")

    def get_llm(self) -> Any:
        """返回当前已绑定工具的默认模型实例。

        Returns:
            当前 ``BaseChatModel`` 实例；未初始化时为 ``None``
        """
        return self._llm

    def bind_tools(self, tools: List) -> "LLMService":
        """把工具绑定到默认模型实例上。

        Args:
            tools: 要绑定的工具列表

        Returns:
            自身，便于链式调用
        """
        if self._llm:
            self._bound_tools = tools
            self._llm = self._llm.bind_tools(tools)
            logger.debug("tools_bound_to_llm", tool_count=len(tools))
        return self

    # ------------------------------------------------------------------
    # 内部实现
    # ------------------------------------------------------------------

    @retry(
        stop=stop_after_attempt(settings.MAX_LLM_CALL_RETRIES),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((RateLimitError, APITimeoutError, APIError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    async def _invoke_with_retry(self, llm: Any, messages: LanguageModelInput) -> Any:
        """对单个模型发起调用，失败按指数退避重试。

        Args:
            llm: 任意 LangChain ``Runnable``（裸模型或结构化输出链）
            messages: 要发送的消息

        Returns:
            Runnable 的响应（``BaseMessage`` 或 ``BaseModel`` 实例）

        Raises:
            Exception: 重试次数用尽后原样抛出——退避路径上是 OpenAI 的异常类型，
                其他情况则是各供应商 Runnable 自己抛出的异常
        """
        try:
            response = await llm.ainvoke(messages)
            logger.debug("llm_call_successful")
            return response
        except (RateLimitError, APITimeoutError, APIError) as e:
            logger.warning(
                "llm_call_failed_retrying",
                error_type=type(e).__name__,
                error=str(e),
                exc_info=True,
            )
            raise
        except (TypeError, AttributeError, KeyError):
            # 这几类是代码写错了，不是供应商故障，直接抛出，
            # 不要误记成「LLM 调用失败」去触发降级。
            raise
        except Exception as e:
            # 上面的退避重试只匹配 OpenAI 异常，而注册表里可能挂着别的供应商，
            # 它们有自己的异常体系；这里必须宽口径捕获，才能让
            # ``_fallback_loop`` 无论谁失败都能切到下一个模型。
            logger.error(
                "llm_call_failed",
                error_type=type(e).__name__,
                error=str(e),
            )
            raise

    def _switch_to_next_model(self) -> bool:
        """把默认模型切到注册表里的下一个（环形）。

        同时更新 ``self._llm`` 与 ``self._current_model_index``，
        并重新绑定工具，保证默认路径切换模型后工具能力不丢。

        Returns:
            切换成功返回 ``True``，失败返回 ``False``
        """
        try:
            next_index = (self._current_model_index + 1) % len(LLMRegistry.LLMS)
            next_entry = LLMRegistry.get_model_at_index(next_index)
            logger.warning(
                "switching_to_next_model",
                from_index=self._current_model_index,
                to_index=next_index,
                to_model=next_entry["name"],
            )
            self._current_model_index = next_index
            self._llm = next_entry["llm"]
            if self._bound_tools:
                self._llm = self._llm.bind_tools(self._bound_tools)
            logger.info("model_switched", new_model=next_entry["name"], new_index=next_index)
            return True
        except Exception as e:
            logger.error("model_switch_failed", error=str(e))
            return False

    async def _call_with_fallback(
        self,
        messages: LanguageModelInput,
        model_name: Optional[str],
        response_format: Optional[Type[BaseModel]],
        model_kwargs: dict,
    ) -> Union[BaseMessage, BaseModel]:
        """按走哪条路径准备好「取目标」与「推进下标」两套策略，交给共用降级循环。

        一次性路径（有任意覆盖参数）：
            ``get_target`` 每次都用注册表现建实例；
            ``advance`` 只推进局部下标——绝不触碰 ``self._llm``。

        默认路径（无覆盖参数）：
            ``get_target`` 直接返回已绑定工具的 ``self._llm``；
            ``advance`` 调用 ``_switch_to_next_model``，让绑定关系延续下去。
        """

        def _override_target(idx: int) -> Any:
            """按注册表下标现建实例，必要时链上结构化输出。"""
            base = LLMRegistry.get(LLMRegistry.LLMS[idx]["name"], **model_kwargs)
            if not response_format:
                return base
            # 用 json_mode 而不是 json_schema：前者在各类 OpenAI 兼容端点上都能用，
            # 而 json_schema 与强制 tool_choice 会被推理模型（如 DeepSeek 思考模式）拒绝。
            return base.with_structured_output(response_format, method="json_mode")

        def _default_target(_: int) -> Any:
            """默认路径始终用当前已绑定工具的实例。"""
            return self._llm

        def _default_advance(_: int) -> Optional[int]:
            """默认路径切模型；切失败就返回 None 终止循环。"""
            return self._current_model_index if self._switch_to_next_model() else None

        if model_name or response_format or model_kwargs:
            all_names = LLMRegistry.get_all_names()
            if model_name and model_name not in all_names:
                logger.error("requested_model_not_found", model_name=model_name)
                raise ValueError(
                    f"model '{model_name}' not found in registry. available models: {', '.join(all_names)}"
                )

            start = all_names.index(model_name) if model_name else self._current_model_index
            total = len(LLMRegistry.LLMS)
            get_target: Callable[[int], Any] = _override_target

            def _override_advance(idx: int) -> Optional[int]:
                """一次性路径环形推进局部下标。"""
                return (idx + 1) % total

            advance: Callable[[int], Optional[int]] = _override_advance
        else:
            start = self._current_model_index
            get_target = _default_target
            advance = _default_advance

        return await self._fallback_loop(messages, start, get_target, advance)

    async def _fallback_loop(
        self,
        messages: LanguageModelInput,
        start: int,
        get_target: Callable[[int], Any],
        advance: Callable[[int], Optional[int]],
    ) -> Any:
        """共用的降级循环：依次尝试每个模型，直到有一个成功。

        Args:
            messages: 要发送的消息
            start: 起始注册表下标
            get_target: 给定下标，返回要调用的 ``Runnable``
            advance: 给定当前下标，返回下一个下标；返回 ``None`` 表示终止

        Returns:
            第一个成功的响应

        Raises:
            RuntimeError: 所有模型都尝试完仍失败
        """
        total = len(LLMRegistry.LLMS)
        current = start
        models_tried = 0
        last_error: Optional[Exception] = None

        for models_tried in range(1, total + 1):
            current_name = LLMRegistry.LLMS[current]["name"]
            try:
                return await self._invoke_with_retry(get_target(current), messages)
            except (TypeError, AttributeError, KeyError):
                # 代码错误在每个模型上都会同样失败，立即抛出，
                # 否则会把服务切到一个降级模型上还留着故障。
                raise
            except Exception as e:
                # 这里刻意宽口径：注册表条目可以是任意供应商，各有各的异常体系，
                # 收窄成 OpenAIError 会让非 OpenAI 模型失去降级能力。
                # 传了非法 model_name 的 ValueError 在进入本循环前就抛了。
                last_error = e
                logger.error(
                    "llm_call_failed_after_retries",
                    model=current_name,
                    models_tried=models_tried,
                    total_models=total,
                    error=str(e),
                )
                if models_tried >= total:
                    logger.error(
                        "all_models_failed", models_tried=models_tried, starting_model=LLMRegistry.LLMS[start]["name"]
                    )
                    break
                next_idx = advance(current)
                if next_idx is None:
                    logger.error("failed_to_switch_to_next_model")
                    break
                current = next_idx

        raise RuntimeError(
            f"failed to get response from llm after trying {models_tried} models. last error: {str(last_error)}"
        )


llm_service = LLMService()
