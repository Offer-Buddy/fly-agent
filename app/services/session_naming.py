"""会话自动命名。

新会话收到第一条消息时，本模块按三步走：

  1. 在 Postgres 里原子地「抢占」这条会话（``UPDATE ... WHERE name = ''``），
     从而在多请求 / 多 uvicorn worker 并发下只让一个调用方去请求 LLM。
  2. 先写一个由用户消息截取出来的占位名，保证即使后面 LLM 调用失败，
     会话也有一个说得过去的标题。
  3. 起一个后台 asyncio 任务，用结构化输出让模型生成正式标题并覆盖占位名。
"""

from langchain_core.messages import HumanMessage, SystemMessage
from sqlmodel import (
    Session as DBSession,
    col,
    update,
)

from app.core.config import settings
from app.core.logging import logger
from app.core.metrics import session_names_generated_total
from app.core.prompts import SESSION_TITLE_PROMPT
from app.models.session import Session as ChatSession
from app.schemas.chat import SessionTitle
from app.services.database import database_service
from app.services.llm import llm_service
from app.utils.tasks import create_background_task

# 占位名最大长度，超过就截断
_PLACEHOLDER_MAX = 40


def _build_placeholder(user_message: str) -> str:
    """从用户首条消息截取一个占位会话名。

    Args:
        user_message: 用户的第一条消息

    Returns:
        str: 压缩空白并截断后的占位名，取不到内容时回退为 "New chat"
    """
    cleaned = " ".join(user_message.split())
    return cleaned[:_PLACEHOLDER_MAX].rstrip() or "New chat"


def _claim_session(session_id: str, placeholder: str) -> bool:
    """抢占会话，只有抢到的那一方返回 True。

    单条 ``UPDATE … WHERE name = ''`` 完成抢占，并发场景下
    只有一个调用方会拿到 ``rowcount == 1``。

    Args:
        session_id: 会话 ID
        placeholder: 抢占成功时写入的占位名

    Returns:
        bool: 本次调用是否抢到命名权
    """
    with DBSession(database_service.engine) as db:
        stmt = (
            update(ChatSession)
            .where(col(ChatSession.id) == session_id, col(ChatSession.name) == "")
            .values(name=placeholder)
        )
        result = db.exec(stmt)
        db.commit()
        return (result.rowcount or 0) == 1


async def _persist_session_name(session_id: str, user_message: str) -> None:
    """调用 LLM 生成会话标题并落库。

    Args:
        session_id: 会话 ID
        user_message: 用户首条消息（截断后作为生成依据）
    """
    try:
        result = await llm_service.call(
            [
                SystemMessage(content=SESSION_TITLE_PROMPT),
                HumanMessage(content=user_message[:500]),
            ],
            model_name=settings.DEFAULT_LLM_MODEL,
            response_format=SessionTitle,
            max_tokens=32,
            temperature=0.3,
        )
        await database_service.update_session_name(session_id, result.title)
        session_names_generated_total.labels(status="success").inc()
        logger.info("session_name_generated", session_id=session_id, name=result.title)
    except Exception:
        # 命名失败不影响对话，只打点 + 记日志
        session_names_generated_total.labels(status="error").inc()
        logger.exception("session_name_generation_failed", session_id=session_id)


def maybe_name_session(session_id: str, session_name: str, messages: list) -> None:
    """会话还没名字时，触发自动命名。

    任意聊天接口都可以安全调用：并发请求会被 Postgres 的抢占语句去重。

    Args:
        session_id: 会话 ID
        session_name: 当前会话名，非空说明已命名，直接返回
        messages: 本轮消息列表
    """
    if session_name:
        return
    first_user_msg = next((m.content for m in messages if m.role == "user"), None)
    if not first_user_msg:
        return
    if _claim_session(session_id, _build_placeholder(first_user_msg)):
        create_background_task(_persist_session_name(session_id, first_user_msg))
