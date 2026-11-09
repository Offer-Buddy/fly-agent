#!/usr/bin/env python
"""把 Langfuse 里的追踪数据汇成一张终端报表。

定位：**不替代 Langfuse 界面，只补它不擅长的那一块**——
界面擅长查「某一次请求发生了什么」，不擅长回答「这周一共花了多少、
延迟是不是在变差」。这个脚本拉最近一段时间的 trace，聚合成三张表，
适合放进日报、周会、成本复盘。

用法::

    uv run python scripts/trace_report.py                  # 最近 24 小时
    uv run python scripts/trace_report.py --hours 168       # 最近 7 天
    uv run python scripts/trace_report.py --days 7          # 按天出趋势

退出码：
    0 = 正常输出；1 = 追踪未启用或拉取失败
"""

import argparse
import sys
from collections import defaultdict
from datetime import (
    datetime,
    timedelta,
)
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings
from app.core.observability import (
    get_client,
    langfuse_init,
)

console = Console()


def _percentile(values: list[float], ratio: float) -> float:
    """取分位数（最近秩法，样本少时也不会插值出假数据）。

    Args:
        values: 样本列表
        ratio: 分位比例，例如 0.95 表示 P95

    Returns:
        float: 分位数值；样本为空返回 0
    """
    if not values:
        return 0.0

    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(ratio * (len(ordered) - 1))))
    return ordered[index]


def _short(text: object, limit: int = 34) -> str:
    """把任意值压成一行短文本，用于表格展示。

    Args:
        text: 原始值
        limit: 最大字符数

    Returns:
        str: 截断后的单行文本
    """
    if text is None:
        return "-"

    flat = " ".join(str(text).split())
    if not flat:
        return "-"

    return flat[:limit] + ("…" if len(flat) > limit else "")


def _build_overview(traces: list) -> Table:
    """汇总核心指标：量、钱、时延、活跃度。

    Args:
        traces: trace 列表

    Returns:
        Table: 可直接打印的 rich 表格
    """
    latencies = [float(t.latency or 0) for t in traces]
    costs = [float(t.total_cost or 0) for t in traces]
    sessions = {t.session_id for t in traces if t.session_id}
    users = {t.user_id for t in traces if t.user_id}
    scored = [t for t in traces if t.scores]

    table = Table(title="① 概览", show_header=True, header_style="bold")
    table.add_column("指标", style="cyan", no_wrap=True)
    table.add_column("数值", justify="right")

    table.add_row("trace 数", f"{len(traces)}")
    table.add_row("会话数 / 用户数", f"{len(sessions)} / {len(users)}")
    table.add_row("总成本", f"${sum(costs):.4f}")
    table.add_row("单条均价", f"${(sum(costs) / len(traces)):.6f}" if traces else "$0")
    table.add_row("延迟 P50 / P95", f"{_percentile(latencies, 0.5):.2f}s / {_percentile(latencies, 0.95):.2f}s")
    table.add_row("最慢一条", f"{max(latencies):.2f}s" if latencies else "-")
    table.add_row("已打分 trace", f"{len(scored)} / {len(traces)}")

    return table


def _build_daily(traces: list) -> Table:
    """按天分桶，看量与成本是不是在往上走。

    Args:
        traces: trace 列表

    Returns:
        Table: 按天聚合的表格
    """
    buckets: dict[str, list] = defaultdict(list)
    for trace in traces:
        buckets[trace.timestamp.strftime("%Y-%m-%d")].append(trace)

    table = Table(title="② 按天", show_header=True, header_style="bold")
    table.add_column("日期", style="cyan", no_wrap=True)
    table.add_column("trace", justify="right")
    table.add_column("成本", justify="right")
    table.add_column("P95 延迟", justify="right")

    for day in sorted(buckets):
        group = buckets[day]
        cost = sum(float(t.total_cost or 0) for t in group)
        p95 = _percentile([float(t.latency or 0) for t in group], 0.95)
        table.add_row(day, str(len(group)), f"${cost:.4f}", f"{p95:.2f}s")

    return table


def _build_top(traces: list, limit: int) -> Table:
    """挑出最贵的几条，方便回头查是不是提示词太长或工具在绕圈。

    Args:
        traces: trace 列表
        limit: 取前几条

    Returns:
        Table: 最贵 trace 的明细表
    """
    ranked = sorted(traces, key=lambda t: float(t.total_cost or 0), reverse=True)[:limit]

    table = Table(title=f"③ 最贵 {limit} 条", show_header=True, header_style="bold")
    table.add_column("时间", no_wrap=True)
    table.add_column("会话", no_wrap=True)
    table.add_column("耗时", justify="right", no_wrap=True)
    table.add_column("成本", justify="right", no_wrap=True)
    table.add_column("输入摘要")

    for trace in ranked:
        table.add_row(
            trace.timestamp.strftime("%m-%d %H:%M"),
            _short(trace.session_id, 12),
            f"{float(trace.latency or 0):.2f}s",
            f"${float(trace.total_cost or 0):.4f}",
            _short(trace.input),
        )

    return table


def main() -> int:
    """解析参数、拉数据、打印报表。

    Returns:
        int: 进程退出码
    """
    parser = argparse.ArgumentParser(description="Langfuse 追踪数据终端报表")
    parser.add_argument("--hours", type=int, default=24, help="统计最近多少小时，默认 24")
    parser.add_argument("--limit", type=int, default=200, help="最多拉多少条 trace，默认 200")
    parser.add_argument("--top", type=int, default=5, help="最贵明细取前几条，默认 5")
    args = parser.parse_args()

    if not settings.LANGFUSE_TRACING_ENABLED:
        console.print("[red]追踪未启用[/red]：先设 LANGFUSE_TRACING_ENABLED=true。")
        return 1

    langfuse_init()
    client = get_client()
    if client is None:
        console.print("[red]客户端没建起来[/red]：检查 key 是否为空。")
        return 1

    since = datetime.now() - timedelta(hours=args.hours)
    console.print(Panel.fit("Langfuse 追踪报表"))
    console.print(f"统计窗口：{since:%m-%d %H:%M} 起，最多 {args.limit} 条\n")

    try:
        traces = client.api.trace.list(
            from_timestamp=since,
            order_by="timestamp.desc",
            limit=args.limit,
        ).data
    except Exception as error:
        console.print(f"[red]拉取失败[/red]：{error}")
        return 1

    if not traces:
        console.print("[yellow]这段时间没有任何 trace。[/yellow]")
        console.print("三个常见原因：① 应用没跑过真实请求 ② 关掉了追踪开关 ③ 命令行脚本忘了 flush。")
        return 1

    console.print(_build_overview(traces))
    console.print(_build_daily(traces))
    console.print(_build_top(traces, args.top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
