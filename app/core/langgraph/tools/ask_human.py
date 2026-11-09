"""LangGraph 的人类确认工具（Human-in-the-loop）。

提供一个会暂停图执行、等待用户确认后再继续的工具，
用于删除数据、发邮件、支付等不可逆操作前的二次确认。
"""

from langchain_core.tools import tool
from langgraph.types import interrupt


@tool
def ask_human(question: str) -> str:
    """Pause execution and ask the human a question before proceeding.

    Use this tool whenever you need clarification, confirmation, or additional
    input from the user before taking a significant action (e.g. deleting data,
    sending emails, making purchases, or any irreversible operation).

    Args:
        question: The question to ask the human.

    Returns:
        str: The human's response.
    """
    # 注意：上面这段 docstring 不是普通注释。LangChain 的 @tool 会把它当作
    # 工具描述写进提示词发给模型，翻译成中文会改变模型的工具选择行为，故保留英文。
    user_response = interrupt(question)
    return str(user_response)
