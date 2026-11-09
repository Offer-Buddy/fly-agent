"""LangGraph 可用工具集合。

把自定义工具集中在这里导出，图构建时直接 import ``tools`` 列表即可；
新增工具记得同时加进下面的列表，否则模型看不到它。
"""

from langchain_core.tools.base import BaseTool

from .ask_human import ask_human
from .duckduckgo_search import duckduckgo_search_tool

tools: list[BaseTool] = [duckduckgo_search_tool, ask_human]
