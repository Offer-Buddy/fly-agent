"""法条检索工具 —— 让 Agent 能查中国现行法律条文。

这是整条 RAG 链路的**唯一出口**：Agent 不需要知道 pgvector、BM25、RRF 是什么，
只管调这个工具、读懂返回的 ``status``。

返回 JSON 而不是纯文本，是因为「检索质量」本身要能传递：
``low_quality`` 和 ``no_relevant_result`` 必须让模型区别对待——
前者要换关键词重搜，后者要老实说没查到。只返回一个条文列表，
模型无从判断该不该相信它，就会开始编。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import tool

from app.core.logging import logger
from app.services import rag
from app.services.rag.payload import to_payload


def _serialize(result: Any) -> str:
    """把检索结果序列化成给模型看的 JSON。

    结构由 ``app.services.rag.payload.to_payload`` 定义，MCP 通道用的是同一个函数，
    两边的 ``status`` 口径必须一致——改契约只改那一处。

    Args:
        result: ``RetrievalResult``

    Returns:
        str: JSON 字符串（中文不转义，便于模型读）
    """
    return json.dumps(to_payload(result), ensure_ascii=False)


@tool
async def legal_search_tool(query: str, top_k: int = 5) -> str:
    """本地法条检索：输入一句法律问题或关键词，返回最相关的中国现行法条原文与相关度。

    什么时候用：任何需要条文依据的问题（「这合不合法」「依据哪条」「能赔多少」），
    在给结论之前先调它。不要凭记忆背法条——背错条号比说「查不到」更糟。

    参数：
    - query：核心法律关系词，如「房东扣押金」「违法解除劳动合同」「七天无理由退货」；
      要查特定条文就直接写「民法典第358条」。不要传整段口语，越短越准。
    - top_k：返回条数，默认 5，常用 3~10。

    返回（JSON 字符串）：
    - status：found 可直接引用；low_quality 最高分低于阈值，须换关键词或说明依据不足；
      no_relevant_result 未命中；unranked 未启用精排。
    - top_rerank_score：最高分，判断可信度用；results[]：law_name、article_no、
      hierarchy、content、rerank_score。
    - hint：非空时按它执行。

    约束：同一 query 不要重复调用；没查到时换更短的关键词或拆成多个小问题，
    而不是换个说法反复试。
    """
    if not rag.is_enabled():
        return json.dumps(
            {"status": "disabled", "results": [], "hint": "法律知识库未启用（RAG_ENABLED=false）。"},
            ensure_ascii=False,
        )

    try:
        retriever = await rag.get_retriever()
        result = await retriever.retrieve(query, top_k=top_k)
        return _serialize(result)
    except Exception as error:  # noqa: BLE001 - 工具层兜底：检索失败不该打断整轮对话
        logger.exception("legal_search_tool_failed", query=query, error=str(error))
        return json.dumps(
            {
                "status": "error",
                "results": [],
                "hint": "检索服务异常，先告知用户暂时查不了法条，不要编造条文；可提示稍后重试。",
                "error": str(error),
            },
            ensure_ascii=False,
        )
