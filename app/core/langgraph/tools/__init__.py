"""LangGraph 可用工具集合。

把自定义工具集中在这里导出，图构建时直接 import ``tools`` 列表即可；
新增工具记得同时加进下面的列表，否则模型看不到它。

``legal_search`` 只在 ``RAG_ENABLED=true`` 时注册：知识库没建好就让模型看见
一个必然失败的工具，它会把整轮对话浪费在重试上。
"""

from langchain_core.tools.base import BaseTool

from app.core.config import settings

from .ask_human import ask_human
from .duckduckgo_search import duckduckgo_search_tool
from .legal_search import legal_search_tool

tools: list[BaseTool] = [duckduckgo_search_tool, ask_human]

if settings.RAG_ENABLED:
    tools.append(legal_search_tool)
