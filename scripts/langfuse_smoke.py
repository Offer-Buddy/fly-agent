#!/usr/bin/env python
"""Langfuse 链路自检：三分钟内证明「key 通了、数据到了、界面能看到」。

很多项目「接入了追踪」却一条数据都没有，原因通常在三个地方：
配置没生效、网络打不通、进程退出前没 flush。这个脚本把这三件事
一次性验掉，不依赖大模型，也不会产生真实调用费用。

用法::

    uv run python scripts/langfuse_smoke.py          # 或 make trace-smoke

退出码：
    0 = 三项检查全通过；1 = 有检查未通过
"""

import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.observability import (
    flush,
    get_client,
    langfuse_init,
    trace_attributes,
    trace_url,
)

console = Console()

# 自检用的假数据：形状与真实链路一致，但不发任何模型请求
FAKE_QUESTION = "自检：这条 trace 应该出现在 Langfuse 的 Traces 列表里"
FAKE_ANSWER = "自检成功。如果能看到这条，说明上报链路是通的。"


def check_config() -> bool:
    """检查三个必填配置项是否齐全。

    Returns:
        bool: 配置可用返回 True
    """
    rows = [
        ("LANGFUSE_TRACING_ENABLED", str(settings.LANGFUSE_TRACING_ENABLED)),
        ("LANGFUSE_HOST", settings.LANGFUSE_HOST or "(空)"),
        ("LANGFUSE_PUBLIC_KEY", _mask(settings.LANGFUSE_PUBLIC_KEY)),
        ("LANGFUSE_SECRET_KEY", _mask(settings.LANGFUSE_SECRET_KEY)),
    ]

    table = Table(title="① 配置", show_header=True, header_style="bold")
    table.add_column("配置项", style="cyan", no_wrap=True)
    table.add_column("当前值")
    for key, value in rows:
        table.add_row(key, value)
    console.print(table)

    if not settings.LANGFUSE_TRACING_ENABLED:
        console.print("[red]追踪开关是关的[/red]：把 LANGFUSE_TRACING_ENABLED 设成 true 再跑。")
        return False

    if not settings.LANGFUSE_PUBLIC_KEY or not settings.LANGFUSE_SECRET_KEY:
        console.print("[red]公钥或私钥为空[/red]：去 Langfuse 项目设置里复制 pk- / sk- 两个 key。")
        return False

    return True


def _mask(value: str) -> str:
    """把密钥打码，只留前 6 位后 4 位。

    Args:
        value: 原始密钥

    Returns:
        str: 打码后的字符串
    """
    if not value:
        return "(空)"
    if len(value) <= 12:
        return value[:2] + "***"
    return f"{value[:6]}…{value[-4:]}"


def check_auth() -> bool:
    """调一次鉴权接口，确认 key 与网络都可用。

    Returns:
        bool: 鉴权通过返回 True
    """
    client = get_client()
    if client is None:
        console.print("[red]客户端没建起来[/red]：langfuse_init() 未生效。")
        return False

    try:
        ok = client.auth_check()
    except Exception as error:
        console.print(f"[red]鉴权请求本身失败[/red]：{error}")
        console.print("常见原因：host 写错、公司网络拦了出站、自建服务没起。")
        return False

    if ok:
        console.print("② 鉴权 [green]通过[/green]")
        return True

    console.print("[red]② 鉴权失败[/red]：key 与 host 不匹配（云端 / 自建不能混用）。")
    return False


def send_probe_trace() -> str | None:
    """发一条带 span 与 generation 的探针 trace。

    Returns:
        str | None: trace ID；发送失败返回 None
    """
    client = get_client()
    if client is None:
        return None

    probe_session = "smoke-session"
    trace_id: str | None = None
    with trace_attributes(session_id=probe_session, user_id="smoke-user", tags=["smoke"]):
        with client.start_as_current_span(name="smoke_root", input=FAKE_QUESTION) as span:
            with client.start_as_current_observation(
                name="smoke_llm_call",
                as_type="generation",
                model=settings.DEFAULT_LLM_MODEL,
                model_parameters={"temperature": settings.DEFAULT_LLM_TEMPERATURE},
                input=[{"role": "user", "content": FAKE_QUESTION}],
                output=FAKE_ANSWER,
                usage_details={"input": 24, "output": 18, "total": 42},
                cost_details={"total": 0.00002},
            ):
                # 真实场景这里是一次模型调用；自检只造形状，不花钱
                pass
            span.update(output=FAKE_ANSWER)
            trace_id = span.trace_id

    return trace_id


def main() -> int:
    """依次跑三项检查并打印结论。

    Returns:
        int: 进程退出码
    """
    console.print(Panel.fit("Langfuse 链路自检"))

    if not check_config():
        return 1

    langfuse_init()
    if not check_auth():
        return 1

    trace_id = send_probe_trace()
    if trace_id is None:
        console.print("[red]③ 探针 trace 没能创建[/red]")
        return 1

    console.print(f"③ 探针 trace 已排队 [green]{trace_id}[/green]")

    # 这一步是整段脚本里最关键的一行：不 flush，进程退出时缓冲区直接丢
    flush()

    url = trace_url(trace_id)
    if url:
        console.print(f"\n去界面对一下：[blue]{url}[/blue]")
    else:
        console.print(f"\n去界面里搜 trace id：[blue]{trace_id}[/blue]")

    console.print(
        "\n[bold]验收标准[/bold]：Traces 里能看到 name=chat 之外的 smoke_root，"
        "点进去有子节点 smoke_llm_call，Session 显示 smoke-session。"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
