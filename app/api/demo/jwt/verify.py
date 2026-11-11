"""验收脚本：把三档强度与两个反例逐条打一遍。

不起网络，用 TestClient 直接调用，输出就是 README 里那张状态码表。
在仓库根目录跑：``python -m app.api.demo.jwt.verify``
"""

from typing import Optional

from fastapi.testclient import TestClient

from app.api.demo.jwt.app import app
from app.utils.auth import create_access_token

client = TestClient(app)
PREFIX = "/demo/jwt"

# 四枚令牌，覆盖「正确身份」「拿错身份」「签名被改」三种情况
USER_TOKEN = create_access_token("1").access_token
SESSION_TOKEN = create_access_token("sess-001").access_token
OTHER_SESSION_TOKEN = create_access_token("sess-009").access_token
TAMPERED_TOKEN = USER_TOKEN[:-3] + "xyz"


def hit(method: str, path: str, label: str, token: Optional[str] = None) -> None:
    """发一个请求并打印状态码。

    Args:
        method: HTTP 方法，小写
        path: 去掉前缀的路径
        label: 这一行的说明
        token: 带上就放进 Authorization 头
    """
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    response = getattr(client, method)(PREFIX + path, headers=headers)
    body = response.json()
    detail = body.get("detail", "") if isinstance(body, dict) else ""
    print(f"{label:<26}{method.upper():<7}{path:<17}-> {response.status_code} {detail}")


def main() -> None:
    """跑完全部用例。"""
    print("--- 一、公开：不验 ---")
    hit("get", "/ping", "探活")
    hit("get", "/feed", "可选档：无令牌")
    hit("get", "/feed", "可选档：坏令牌静默降级", TAMPERED_TOKEN)

    print("\n--- 二、用户令牌 ---")
    hit("get", "/me", "无令牌")
    hit("get", "/me", "坏令牌", TAMPERED_TOKEN)
    hit("get", "/me", "格式非法", "not-a-jwt")
    hit("get", "/me", "拿会话令牌冒充用户", SESSION_TOKEN)
    hit("get", "/me", "正确令牌", USER_TOKEN)
    hit("get", "/sessions", "只列自己的会话", USER_TOKEN)

    print("\n--- 三、会话令牌 ---")
    hit("delete", "/messages", "无令牌")
    hit("post", "/chat", "拿用户令牌调会话接口", USER_TOKEN)
    hit("post", "/chat", "正确令牌", SESSION_TOKEN)
    hit("get", "/messages", "函数内没有鉴权代码", SESSION_TOKEN)

    print("\n--- 四、反例 ---")
    hit("get", "/cannot-cancel", "端点想取消路由级依赖")
    hit("get", "/cannot-cancel", "带令牌才进得去", USER_TOKEN)
    hit("get", "/user-secrets", "漏挂：直接 200")
    hit("get", "/reports", "挂载时补的：无令牌")
    hit("get", "/reports", "挂载时补的：有令牌", USER_TOKEN)

    print("\n--- 五、越权检查 ---")
    hit("post", "/chat", "别人的会话令牌（鉴权过了）", OTHER_SESSION_TOKEN)
    print("   注意：鉴权通过不等于有权访问——授权要靠 session.user_id 比对")


if __name__ == "__main__":
    main()
