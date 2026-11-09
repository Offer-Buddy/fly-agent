"""输入清洗工具：防 XSS 与注入的基础处理。"""

import html
import re
from typing import (
    Any,
    Dict,
    List,
)


def sanitize_string(value: str) -> str:
    """清洗字符串，降低 XSS 与注入风险。

    做四件事：转成字符串 → HTML 转义 → 去掉转义后的 script 标签 → 去掉空字节。

    Args:
        value: 待清洗的字符串

    Returns:
        str: 清洗后的字符串
    """
    # 非字符串先转成字符串
    if not isinstance(value, str):
        value = str(value)

    # HTML 转义，防止前端直接渲染时被执行
    value = html.escape(value)

    # 转义之后 script 标签变成 &lt;script&gt; 形式，再整体删掉
    value = re.sub(r"&lt;script.*?&gt;.*?&lt;/script&gt;", "", value, flags=re.DOTALL)

    # 去掉空字节
    value = value.replace("\0", "")

    return value


def sanitize_email(email: str) -> str:
    """清洗并校验邮箱格式。

    Args:
        email: 原始邮箱

    Returns:
        str: 清洗并转小写后的邮箱

    Raises:
        ValueError: 格式不符合邮箱规则
    """
    # 先做通用清洗
    email = sanitize_string(email)

    # 再做一次格式校验（简单正则，够用即可）
    if not re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email):
        raise ValueError("Invalid email format")

    return email.lower()


def sanitize_dict(data: Dict[str, Any]) -> Dict[str, Any]:
    """递归清洗字典里的所有字符串值。

    Args:
        data: 待清洗的字典

    Returns:
        Dict[str, Any]: 清洗后的新字典（不修改原对象）
    """
    sanitized = {}
    for key, value in data.items():
        if isinstance(value, str):
            sanitized[key] = sanitize_string(value)
        elif isinstance(value, dict):
            sanitized[key] = sanitize_dict(value)
        elif isinstance(value, list):
            sanitized[key] = sanitize_list(value)
        else:
            sanitized[key] = value
    return sanitized


def sanitize_list(data: List[Any]) -> List[Any]:
    """递归清洗列表里的所有字符串值。

    Args:
        data: 待清洗的列表

    Returns:
        List[Any]: 清洗后的新列表
    """
    sanitized = []
    for item in data:
        if isinstance(item, str):
            sanitized.append(sanitize_string(item))
        elif isinstance(item, dict):
            sanitized.append(sanitize_dict(item))
        elif isinstance(item, list):
            sanitized.append(sanitize_list(item))
        else:
            sanitized.append(item)
    return sanitized


def validate_password_strength(password: str) -> bool:
    """校验密码强度。

    规则与 ``app/schemas/auth.py`` 的校验器保持一致：
    至少 8 位，且包含大写字母、小写字母、数字与特殊字符。

    Args:
        password: 明文密码

    Returns:
        bool: 校验通过返回 True

    Raises:
        ValueError: 不满足某项要求，异常消息即为原因
    """
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters long")

    if not re.search(r"[A-Z]", password):
        raise ValueError("Password must contain at least one uppercase letter")

    if not re.search(r"[a-z]", password):
        raise ValueError("Password must contain at least one lowercase letter")

    if not re.search(r"[0-9]", password):
        raise ValueError("Password must contain at least one number")

    if not re.search(r'[!@#$%^&*(),.?":{}|<>]', password):
        raise ValueError("Password must contain at least one special character")

    return True
