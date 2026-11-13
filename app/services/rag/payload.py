"""检索结果的对外契约 —— 一份 JSON 结构，两个通道共用。

法条检索现在有两个出口：

- 进程内：``app.core.langgraph.tools.legal_search`` 给 LangGraph 的 Agent 用
- 跨进程：``app.mcp.legal.server`` 给任意 MCP 客户端（WorkBuddy / Cursor / 别的 Agent）用

两个出口必须吐**同一种结构**，否则模型会看到两套 ``status`` 口径，
「什么算可信」的定义就散了。所以序列化只写一次，放这里。

这里的函数不认识 pgvector、不认识 FastMCP、不认识 LangChain，
只吃一个 ``RetrievalResult``，吐一个纯 dict —— 换个传输协议不用改。
"""

from __future__ import annotations

from app.services.rag.hybrid import RetrievalResult


def hint_for(status: str) -> str:
    """按检索状态给出下一步建议。

    Args:
        status: ``RetrievalResult.status``

    Returns:
        str: 给模型看的操作建议；状态正常时为空串
    """
    if status == "low_quality":
        return "最高分低于阈值：把问题拆成更短的法律关键词（如「违法解除劳动合同 赔偿」）再搜一次，或说明依据不足。"
    if status == "no_relevant_result":
        return "没有检索到相关法条：换用更通用的法律术语重试，仍无结果就明确告知用户样本库未覆盖，不要凭空作答。"
    if status == "unranked":
        return "精排模型未启用：结果按融合顺序返回，引用前请自行核对条文是否切题。"
    return ""


def to_payload(result: RetrievalResult) -> dict:
    """把检索结果转成对外 JSON 结构。

    Args:
        result: ``RetrievalResult``

    Returns:
        dict: 含 ``status`` / ``top_rerank_score`` / ``results`` / ``hint`` / ``timings_ms``
    """
    return {
        "status": result.status,
        "query_normalized": result.query,
        "top_rerank_score": round(result.top_score, 4) if result.top_score is not None else None,
        "threshold": result.threshold,
        "results": [
            {
                "law_name": chunk.law_name,
                "article_no": chunk.article_no,
                "hierarchy": chunk.hierarchy,
                "content": chunk.content,
                "rerank_score": round(chunk.score, 4) if result.reranked else None,
            }
            for chunk in result.chunks
        ],
        "hint": hint_for(result.status),
        "timings_ms": result.timings,
    }
