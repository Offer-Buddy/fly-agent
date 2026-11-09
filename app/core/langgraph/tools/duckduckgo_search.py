"""DuckDuckGo 联网搜索工具。

封装 LangChain 社区版的搜索工具，最多返回 10 条结果，
并把工具内部异常转成文本结果，避免一次搜索失败打断整个对话。
"""

from langchain_community.tools import DuckDuckGoSearchResults

duckduckgo_search_tool = DuckDuckGoSearchResults(num_results=10, handle_tool_error=True)
