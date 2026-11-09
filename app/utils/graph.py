"""图相关的工具函数：消息转换、token 计数、上下文裁剪、内容抽取。"""

from typing import cast

import tiktoken
from langchain_core.messages import (
    BaseMessage,
    HumanMessage,
)
from langchain_core.messages import trim_messages as _trim_messages

from app.core.config import settings
from app.core.logging import logger
from app.schemas import Message

# 在模块级缓存 tiktoken 编码器：线程安全，且避免每次计数都重新加载词表
try:
    _TIKTOKEN_ENCODING = tiktoken.encoding_for_model(settings.DEFAULT_LLM_MODEL)
except KeyError:
    # 自定义模型名（如 deepseek-*）tiktoken 不认识，退回通用词表
    _TIKTOKEN_ENCODING = tiktoken.get_encoding("cl100k_base")


def _count_tokens_tiktoken(messages: list) -> int:
    """用 tiktoken 在本地估算 token 数，不产生 API 调用。

    Args:
        messages: 消息列表，元素可以是 dict 或 BaseMessage

    Returns:
        int: 估算的 token 总数
    """
    num_tokens = 0
    for message in messages:
        # 每条消息都有固定开销（role、name 等字段）
        num_tokens += 4
        if isinstance(message, dict):
            for _, value in message.items():
                if isinstance(value, str):
                    num_tokens += len(_TIKTOKEN_ENCODING.encode(value))
        elif isinstance(message, BaseMessage):
            content = message.content
            if isinstance(content, str):
                num_tokens += len(_TIKTOKEN_ENCODING.encode(content))
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, str):
                        num_tokens += len(_TIKTOKEN_ENCODING.encode(block))
                    elif isinstance(block, dict) and "text" in block:
                        num_tokens += len(_TIKTOKEN_ENCODING.encode(block["text"]))
    num_tokens += 2  # 回复侧还要预留 assistant 的起始标记
    return num_tokens


def dump_messages(messages: list[Message]) -> list[dict]:
    """把 pydantic 消息对象转成普通字典列表。

    Args:
        messages: 待转换的消息

    Returns:
        list[dict]: 转换后的字典列表
    """
    return [message.model_dump() for message in messages]


def extract_text_content(content: str | list) -> str:
    """从模型返回的 content 中抽出纯文本。

    需要同时兼容两种格式：简单的字符串，以及 GPT-5 / Responses API 那种
    结构化的内容块列表，例如
    ``[{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}]``。

    Args:
        content: LangChain BaseMessage 的原始 content

    Returns:
        str: 抽出的纯文本；没有可抽取内容时返回空串
    """
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "reasoning":
                # 思考过程不对外输出，只记一条调试日志
                logger.debug(
                    "reasoning_block_received",
                    reasoning_id=block.get("id"),
                    has_summary=bool(block.get("summary")),
                )
    return "".join(parts)


def latest_user_text(messages: list[BaseMessage]) -> str:
    """取消息列表里最近一条用户消息的文本。

    从后往前找，因此会自动跳过助手回复、工具调用与工具结果。
    没有用户消息时返回空串。

    Args:
        messages: LangGraph 的消息列表

    Returns:
        str: 最近一条用户消息的纯文本
    """
    last_user = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    return extract_text_content(last_user.content) if last_user else ""


def process_llm_response(response: BaseMessage) -> BaseMessage:
    """把模型响应规整成 ``content`` 一定是纯字符串的形式。

    不同供应商的 content 格式不一致（字符串 / 内容块列表），
    下游统一按字符串处理可以省掉一堆判断。

    Args:
        response: 模型原始响应

    Returns:
        BaseMessage: 同一个实例，``content`` 已被替换为纯字符串
    """
    if isinstance(response.content, list):
        response.content = extract_text_content(response.content)
        logger.debug(
            "processed_structured_content",
            content_block_count=len(response.content),
            extracted_length=len(response.content),
        )
    return response


def prepare_messages(messages: list[Message], system_prompt: str) -> list[Message]:
    """组装最终发给 LLM 的消息：系统提示 + 按 token 预算裁剪后的历史。

    裁剪策略是「保留最近的」，且必须从用户消息开始，避免出现
    「助手回复在开头」这种模型无法处理的序列。

    Args:
        messages: 历史消息
        system_prompt: 本次要使用的系统提示

    Returns:
        list[Message]: 可直接发送的消息列表
    """
    try:
        trimmed_messages = _trim_messages(
            dump_messages(messages),
            strategy="last",
            token_counter=_count_tokens_tiktoken,
            max_tokens=settings.MAX_TOKENS,
            start_on="human",
            include_system=False,
            allow_partial=False,
        )
    except ValueError as e:
        # 遇到不认识的内容块（如 GPT-5 的 reasoning 块）时，计数会失败；
        # 这种情况放弃裁剪，把消息原样发出去，总比报错强。
        if "Unrecognized content block type" in str(e):
            logger.warning(
                "token_counting_failed_skipping_trim",
                error=str(e),
                message_count=len(messages),
            )
            trimmed_messages = messages
        else:
            raise

    # langchain-core >= 1.6 把 trim_messages() 的返回类型标成了 BaseMessage，
    # 这里 cast 回声明的返回类型，让 pyright 通过。
    return cast(list[Message], [Message(role="system", content=system_prompt)] + trimmed_messages)
