"""Agent 提示词加载。

三个模板都以 Markdown 文件形式放在本目录下，模块导入时一次性读入内存，
之后每次请求直接复用，避免逐次请求读盘。

注意：``session_title.md`` / ``memory_capture.md`` 等模板正文是喂给大模型的
提示词，保持英文（模型对英文指令的遵循度更稳定），这里只负责读取与变量替换。
"""

import os
from datetime import datetime
from typing import Optional

from app.core.config import settings

_PROMPTS_DIR = os.path.dirname(__file__)

# 模块加载时读取一次模板，后续请求不再做文件 I/O
# 必须显式指定 utf-8：模板正文含中文与弯引号，而 open() 默认按系统 locale
# 解码（中文 Windows 上是 cp936/GBK），会抛 UnicodeDecodeError。
with open(os.path.join(_PROMPTS_DIR, "system.md"), "r", encoding="utf-8") as _f:
    _SYSTEM_PROMPT_TEMPLATE = _f.read()

with open(os.path.join(_PROMPTS_DIR, "session_title.md"), "r", encoding="utf-8") as _f:
    SESSION_TITLE_PROMPT = _f.read()

with open(os.path.join(_PROMPTS_DIR, "memory_capture.md"), "r", encoding="utf-8") as _f:
    _MEMORY_CAPTURE_PROMPT_TEMPLATE = _f.read()


def load_system_prompt(username: Optional[str] = None, **kwargs):
    """渲染系统提示词模板。

    Args:
        username: 当前用户名，传入后会拼接一段「你在和谁对话」的上下文
        **kwargs: 传递给模板的其他变量

    Returns:
        渲染后的系统提示词
    """
    user_context = f"# User\nYou are talking to {username}.\n" if username else ""
    return _SYSTEM_PROMPT_TEMPLATE.format(
        agent_name=settings.PROJECT_NAME + " Agent",
        current_date_and_time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        user_context=user_context,
        **kwargs,
    )


def load_memory_capture_prompt() -> str:
    """渲染「记忆抽取」提示词，并填入当天日期。

    这里用 ``str.replace`` 而不是 ``str.format``：提示词正文里含有
    带花括号的字面 JSON 示例，用 format() 会被当成占位符而报错。

    Returns:
        渲染后的提示词
    """
    return _MEMORY_CAPTURE_PROMPT_TEMPLATE.replace("{current_date}", datetime.now().strftime("%Y-%m-%d"))
