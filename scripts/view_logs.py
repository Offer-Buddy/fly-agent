#!/usr/bin/env python
"""按 UTF-8 正确读取并打印日志文件，顺带能给历史文件补 BOM。

Windows 上的 ``type``、PowerShell 5.1 的 ``Get-Content``、老版记事本都按系统
ANSI 代码页解码文本，简体中文环境即 cp936。日志正文是 UTF-8，被当成 GBK 读就
成了「涓枃涓嶈浆涔夋祴璇」这类乱码。本脚本干三件事：

1. 强制按 UTF-8 读取（``utf-8-sig``，自动剥掉可能存在的 BOM）
2. 把每行 JSON 折成「时间 级别 事件 其余字段」的易读格式
3. ``--fix-bom`` 给历史日志补 BOM，之后资源管理器里双击也能正常看

用法::

    uv run python scripts/view_logs.py                # 今天的日志，最后 50 条
    uv run python scripts/view_logs.py -n 200
    uv run python scripts/view_logs.py --level error --grep 超时
    uv run python scripts/view_logs.py --follow       # 持续跟随（Ctrl+C 退出）
    uv run python scripts/view_logs.py --list         # 列出所有日志文件
    uv run python scripts/view_logs.py --fix-bom      # 给历史日志补 BOM

退出码：
    0 = 正常；1 = 日志目录或目标文件不存在
"""

import argparse
import ctypes
import json
import locale
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text

console = Console()

# UTF-8 BOM 的三个字节，与 app/core/logging.py 里的常量保持一致
UTF8_BOM = b"\xef\xbb\xbf"

# 渲染时不出现在「其余字段」里的键：要么已经在头部单列，要么是调试噪声
RESERVED_KEYS = frozenset(
    {
        "timestamp",
        "level",
        "event",
        "logger",
        "environment",
        "filename",
        "func_name",
        "lineno",
        "module",
        "pathname",
    }
)

LEVEL_STYLES = {
    "debug": "dim",
    "info": "cyan",
    "warning": "yellow",
    "error": "bold red",
    "critical": "bold white on red",
}


def _console_output_encoding() -> str | None:
    """读取当前 Windows 控制台的输出代码页，转成 Python 编码名。

    Returns:
        str | None: 编码名（如 ``cp936``）；非 Windows 或取不到时返回 None
    """
    windll = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    try:
        codepage = windll.kernel32.GetConsoleOutputCP()
    except (AttributeError, OSError):
        return None
    if not codepage:
        return None
    return "utf-8" if codepage == 65001 else f"cp{codepage}"


def configure_output_encoding() -> None:
    """让脚本的输出编码与终端解码方式对齐，避免中文乱码。

    系统里若设了 ``PYTHONUTF8=1`` / ``PYTHONIOENCODING=utf-8``，Python 会固定往
    stdout 写 UTF-8 字节；而 PowerShell 与 cmd 默认按 cp936 解码，中文又变乱码。
    输出被接管（管道、重定向）时主动改回控制台真实代码页，两端就对齐了。

    输出直连终端时不动：那种情况 Python 走 Windows 控制台接口，编码无关，
    改了反而可能出问题。
    """
    if sys.stdout.isatty():
        return

    # Git Bash / MSYS 的管道期待 UTF-8（它自己不做代码页转换），
    # 按 Windows 控制台代码页改反而会把已经正确的输出弄成乱码
    if os.getenv("MSYSTEM"):
        return

    encoding = _console_output_encoding() or locale.getpreferredencoding(False)
    try:
        sys.stdout.reconfigure(encoding=encoding, errors="replace")
    except (AttributeError, OSError, ValueError):
        return


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    Returns:
        argparse.Namespace: 解析结果
    """
    parser = argparse.ArgumentParser(
        description="以 UTF-8 读取并打印结构化日志（解决 Windows 中文乱码）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("-n", "--lines", type=int, default=50, help="打印最后 N 条，默认 50")
    parser.add_argument("--all", action="store_true", help="打印整个文件（忽略 -n）")
    parser.add_argument("--file", type=Path, help="指定日志文件；默认取当天、当前环境的文件")
    parser.add_argument("--log-dir", type=Path, default=Path("logs"), help="日志目录，默认 logs")
    parser.add_argument("--date", help="指定日期，格式 YYYY-MM-DD，默认今天")
    parser.add_argument("--env", help="环境名（development/staging/production/test），默认读 APP_ENV")
    parser.add_argument("--grep", help="只显示整行包含该关键字的记录（不分大小写）")
    parser.add_argument(
        "--level",
        choices=["debug", "info", "warning", "error", "critical"],
        help="只显示不低于该级别的记录",
    )
    parser.add_argument("--raw", action="store_true", help="原样输出 JSON 行，不做字段折叠")
    parser.add_argument("--follow", action="store_true", help="像 tail -f 一样持续输出新增记录")
    parser.add_argument("--list", action="store_true", help="列出日志目录下的所有文件")
    parser.add_argument("--fix-bom", action="store_true", help="给目录下缺 BOM 的日志文件补 UTF-8 BOM")
    return parser.parse_args()


def resolve_env(cli_value: str | None) -> str:
    """确定当前环境名，用于拼日志文件名。

    Args:
        cli_value: 命令行显式指定的环境名

    Returns:
        str: 环境名，默认 development
    """
    if cli_value:
        return cli_value
    return os.getenv("APP_ENV") or os.getenv("ENVIRONMENT") or "development"


def resolve_file(args: argparse.Namespace) -> Path:
    """按参数推出本次要读的日志文件。

    Args:
        args: 命令行参数

    Returns:
        Path: 日志文件路径
    """
    if args.file:
        return args.file
    day = args.date or datetime.now().strftime("%Y-%m-%d")
    return args.log_dir / f"{resolve_env(args.env)}-{day}.jsonl"


def list_log_files(log_dir: Path) -> list[Path]:
    """列出日志目录下的所有 JSONL 文件，按文件名倒序（新的在前）。

    Args:
        log_dir: 日志目录

    Returns:
        list[Path]: 文件路径列表
    """
    return sorted(log_dir.glob("*.jsonl"), key=lambda p: p.name, reverse=True)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    r"""按 UTF-8 读取整个 JSONL 文件并逐行解析。

    用 ``utf-8-sig`` 打开：既保证 UTF-8 解码，又能自动吞掉文件头的 BOM，
    否则第一行的 JSON 会因为多出 ``\\ufeff`` 而解析失败。

    Args:
        path: 日志文件路径

    Returns:
        list[dict[str, Any]]: 解析成功的日志记录，无法解析的行会被跳过
    """
    records: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                # 采集到一半的行（进程被杀）会因为 JSON 不完整而解析失败，直接跳过
                continue
            if isinstance(parsed, dict):
                records.append(parsed)
    return records


def format_value(value: Any) -> str:
    """把字段值折成一行文本。

    Args:
        value: 任意字段值

    Returns:
        str: 单行字符串表示
    """
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_record(record: dict[str, Any]) -> Text:
    """把一条日志记录渲染成带颜色的单行文本。

    Args:
        record: 日志记录字典

    Returns:
        Text: 可直接打印的富文本
    """
    text = Text()

    timestamp = str(record.get("timestamp", ""))
    # ISO 时间只留时分秒毫秒，日期已经体现在文件名上
    clock = timestamp[11:23] if len(timestamp) >= 23 else timestamp
    text.append(f"{clock}  ", style="dim")

    level = str(record.get("level", "")).lower()
    text.append(f"{level.upper():<8}", style=LEVEL_STYLES.get(level, ""))

    text.append(str(record.get("event", "")), style="bold")

    extras = [f"{k}={format_value(v)}" for k, v in record.items() if k not in RESERVED_KEYS]
    if extras:
        text.append("  " + "  ".join(extras), style="dim")
    return text


def matches(record: dict[str, Any], raw_line: str, args: argparse.Namespace) -> bool:
    """判断一条记录是否满足 --grep / --level 过滤条件。

    Args:
        record: 解析后的日志记录
        raw_line: 原始行文本
        args: 命令行参数

    Returns:
        bool: 是否保留该记录
    """
    if args.level:
        order = ["debug", "info", "warning", "error", "critical"]
        current = str(record.get("level", "")).lower()
        if current in order and order.index(current) < order.index(args.level):
            return False

    if args.grep and args.grep.lower() not in raw_line.lower():
        return False
    return True


def iter_raw_lines(path: Path) -> list[str]:
    """按 UTF-8 读出文件的全部原始行（已剥 BOM）。

    Args:
        path: 日志文件路径

    Returns:
        list[str]: 原始行列表
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        return [line.rstrip("\r\n") for line in f if line.strip()]


def print_records(path: Path, args: argparse.Namespace) -> int:
    """打印指定日志文件的记录。

    Args:
        path: 日志文件路径
        args: 命令行参数

    Returns:
        int: 实际打印的记录条数
    """
    lines = iter_raw_lines(path)
    if not args.all and args.lines > 0:
        lines = lines[-args.lines :]

    printed = 0
    for raw_line in lines:
        try:
            record = json.loads(raw_line)
        except json.JSONDecodeError:
            if args.grep and args.grep.lower() not in raw_line.lower():
                continue
            console.print(raw_line, markup=False, highlight=False, soft_wrap=True)
            printed += 1
            continue

        if not isinstance(record, dict):
            continue
        if not matches(record, raw_line, args):
            continue

        if args.raw:
            console.print(raw_line, markup=False, highlight=False, soft_wrap=True)
        else:
            console.print(render_record(record), soft_wrap=True)
        printed += 1
    return printed


def follow(path: Path, args: argparse.Namespace) -> None:
    """持续输出日志文件的新增内容，效果等同 ``tail -f``。

    Args:
        path: 日志文件路径
        args: 命令行参数
    """
    with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
        # 先输出已有内容（按 -n 截断），再从文件末尾继续跟
        existing = f.readlines()
        tail = existing if args.all else existing[-args.lines :]
        for line in tail:
            if line.strip():
                console.print(line.rstrip(), markup=False, highlight=False, soft_wrap=True)
        f.seek(0, os.SEEK_END)

        try:
            while True:
                line = f.readline()
                if not line:
                    time.sleep(0.3)
                    continue
                if line.strip():
                    console.print(line.rstrip(), markup=False, highlight=False, soft_wrap=True)
        except KeyboardInterrupt:
            return


def fix_bom(log_dir: Path) -> int:
    """给目录下缺 UTF-8 BOM 的日志文件补上 BOM。

    Args:
        log_dir: 日志目录

    Returns:
        int: 实际修补的文件数
    """
    fixed = 0
    for path in list_log_files(log_dir):
        try:
            size = path.stat().st_size
            if size == 0:
                continue
            with open(path, "rb") as f:
                head = f.read(3)
            if head == UTF8_BOM:
                continue
            # 只补文件头三个字节，正文一个都不动
            content = path.read_bytes()
            path.write_bytes(UTF8_BOM + content)
            console.print(f"[green]已补 BOM[/green] {path.name}")
            fixed += 1
        except OSError as exc:
            console.print(f"[red]补 BOM 失败[/red] {path.name}: {exc}")
    console.print(f"共修补 {fixed} 个文件")
    return fixed


def main() -> int:
    """脚本入口。

    Returns:
        int: 进程退出码
    """
    configure_output_encoding()
    args = parse_args()

    if not args.log_dir.exists() and not args.file:
        console.print(f"[red]日志目录不存在：[/red]{args.log_dir.resolve()}")
        return 1

    if args.fix_bom:
        fix_bom(args.log_dir)
        return 0

    if args.list:
        files = list_log_files(args.log_dir)
        if not files:
            console.print(f"[yellow]没有任何日志文件：[/yellow]{args.log_dir.resolve()}")
            return 0
        for path in files:
            size_kb = path.stat().st_size / 1024
            console.print(f"{path.name:<40} {size_kb:>10.1f} KB")
        return 0

    path = resolve_file(args)
    if not path.exists():
        console.print(f"[red]日志文件不存在：[/red]{path.resolve()}")
        console.print("用 [bold]--list[/bold] 看看有哪些文件，或用 [bold]--file[/bold] 指定")
        return 1

    if args.follow:
        console.print(f"[dim]跟随 {path.name}（Ctrl+C 退出）[/dim]")
        follow(path, args)
        return 0

    count = print_records(path, args)
    console.print(f"[dim]—— {path.name}，共 {count} 条 ——[/dim]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
