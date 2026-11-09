"""LangGraph 图状态定义。"""

from typing import Annotated

from langgraph.graph.message import add_messages
from pydantic import (
    BaseModel,
    Field,
)


class GraphState(BaseModel):
    """智能体的图状态。

    两个字段都会被 LangGraph 持久化到检查点，因此恢复会话时能拿到完整上下文。

    Attributes:
        messages: 对话消息列表，用 ``add_messages`` 做增量合并而非覆盖
        long_term_memory: 本轮检索到的长期记忆文本，会拼进系统提示
    """

    messages: Annotated[list, add_messages] = Field(default_factory=list, description="会话中的消息列表")
    long_term_memory: str = Field(default="", description="会话的长期记忆")
