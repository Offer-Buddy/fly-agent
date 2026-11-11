"""⑧ 以 AI 客户端的身份验收：拿 token → 列工具 → 调工具。

对应课件第七节。它是**验收探针**，不参与业务，上线不需要它。
作用是把「工具清单对不对、票有没有穿透、身份认没认出来」变成几行能看的输出。

跑法（服务要先起）：
    python -m app.mcp.demo.02_task_app.main
    python -m app.mcp.demo.02_task_app.mcp_client
"""

import asyncio
import os

import httpx
from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

# 默认 8010：本机 8000 常被别的服务占着，换成你的端口改这个环境变量就行
BASE = os.getenv("MCP_BASE", "http://127.0.0.1:8010")


async def main(username: str = "alice") -> None:
    """走完整链路，最后再试一次不带票会被挡住。

    Args:
        username: 用哪个演示账号，默认 alice。
    """
    resp = httpx.post(f"{BASE}/api/token", json={"username": username, "password": f"{username}-pwd"})
    token = resp.json()["access_token"]  # 换票：白名单接口，不用带票
    print(f"拿到 {username} 的 token：{token[:28]}…")

    transport = StreamableHttpTransport(f"{BASE}/mcp/", headers={"Authorization": f"Bearer {token}"})
    async with Client(transport) as client:
        print("工具清单：", [t.name for t in await client.list_tools()])  # ① 先要菜单
        print("我是谁  ：", (await client.call_tool("whoami", {})).data)  # ② 验身份
        print("新建任务：", (await client.call_tool("create_task", {"title": f"{username} 的任务"})).data)
        print("我的任务：", (await client.call_tool("list_tasks", {})).data)  # ③ 读回来

    # 不带 token 再试一次：握手就被网关挡住
    try:
        async with Client(StreamableHttpTransport(f"{BASE}/mcp/")) as client:
            await client.list_tools()
        print("没挡住 —— 有问题")
    except Exception as exc:  # noqa: BLE001 —— 这里就是要把各种失败都打出来
        print("无 token 调工具 →", type(exc).__name__, str(exc)[:60])


if __name__ == "__main__":
    asyncio.run(main())
