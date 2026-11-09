"""聊天相关请求 / 响应模型。"""

import re
from typing import (
    List,
    Literal,
)

from pydantic import (
    BaseModel,
    Field,
    field_validator,
)

from app.schemas.base import BaseResponse


class Message(BaseModel):
    """单条消息。

    Attributes:
        role: 角色，user / assistant / system
        content: 消息正文，1~3000 字
    """

    model_config = {"extra": "ignore"}

    role: Literal["user", "assistant", "system"] = Field(..., description="消息发送者的角色")
    content: str = Field(..., description="消息正文", min_length=1, max_length=3000)

    @field_validator("content")
    @classmethod
    def validate_content(cls, v: str) -> str:
        """做一层基础的输入安全校验。

        Args:
            v: 待校验的正文

        Returns:
            str: 校验通过的正文

        Raises:
            ValueError: 正文含脚本标签或空字节
        """
        # 拦截 <script> 标签，避免前端直接渲染时被注入
        if re.search(r"<script.*?>.*?</script>", v, re.IGNORECASE | re.DOTALL):
            raise ValueError("Content contains potentially harmful script tags")

        # 拦截空字节，Postgres 与部分解析器不接受
        if "\0" in v:
            raise ValueError("Content contains null bytes")

        return v


class ChatRequest(BaseModel):
    """对话请求体。

    Attributes:
        messages: 会话中的消息列表，至少一条
    """

    messages: List[Message] = Field(
        ...,
        description="会话中的消息列表",
        min_length=1,
    )


class ChatResponse(BaseResponse):
    """对话响应体。

    Attributes:
        messages: 本轮产生的消息列表
    """

    messages: List[Message] = Field(..., description="会话中的消息列表")


class StreamResponse(BaseResponse):
    """流式对话的单帧数据。

    Attributes:
        content: 本帧的文本增量
        done: 是否已结束
    """

    content: str = Field(default="", description="本帧的文本内容")
    done: bool = Field(default=False, description="流是否结束")


class SessionTitle(BaseModel):
    """会话标题生成用的结构化输出模型。

    只定义了 ``title`` 一个字段，配合 json_mode 让模型直接吐 JSON，
    避免多字段时模型自由发挥。
    """

    title: str = Field(
        ...,
        min_length=1,
        max_length=60,
    )

    @field_validator("title")
    @classmethod
    def _normalize(cls, v: str) -> str:
        """规整模型输出：压空白、去首尾标点，空标题直接报错。"""
        v = " ".join(v.split()).strip(" \"'`.,:;!?-")
        if not v:
            raise ValueError("empty title after normalization")
        return v
