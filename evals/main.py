#!/usr/bin/env python3
"""LLM 评测的命令行入口。

支持三种跑法：默认（读命令行参数）、``--interactive``（逐项询问）、
``--quick``（全用默认值直接开跑）。

说明：终端里的提示与结果文案保持英文——它们是程序输出，不是文档；
本文件只把注释与 docstring 中文化。
"""

import argparse
import asyncio
import os
import sys
from typing import (
    Any,
    Dict,
    Optional,
)

import colorama
from colorama import (
    Fore,
    Style,
)

# 让脚本能直接 import 到项目内的 app 包
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.config import settings
from app.core.logging import logger
from evals.evaluator import Evaluator

# 默认配置：模型与端点都来自 settings
DEFAULT_CONFIG = {
    "generate_report": True,
    "model": settings.EVALUATION_LLM,
    "api_base": settings.EVALUATION_BASE_URL,
}


def print_title(title: str) -> None:
    """打印带颜色与分隔线的大标题。

    Args:
        title: 标题文字
    """
    print("\n" + "=" * 60)
    print(f"{Fore.CYAN}{Style.BRIGHT}{title.center(60)}{Style.RESET_ALL}")
    print("=" * 60 + "\n")


def print_info(message: str) -> None:
    """打印一条普通信息（绿色）。

    Args:
        message: 信息内容
    """
    print(f"{Fore.GREEN}• {message}{Style.RESET_ALL}")


def print_warning(message: str) -> None:
    """打印一条警告（黄色）。

    Args:
        message: 警告内容
    """
    print(f"{Fore.YELLOW}⚠ {message}{Style.RESET_ALL}")


def print_error(message: str) -> None:
    """打印一条错误（红色）。

    Args:
        message: 错误内容
    """
    print(f"{Fore.RED}✗ {message}{Style.RESET_ALL}")


def print_success(message: str) -> None:
    """打印一条成功提示（绿色对勾）。

    Args:
        message: 提示内容
    """
    print(f"{Fore.GREEN}✓ {message}{Style.RESET_ALL}")


def get_user_input(prompt: str, default: Optional[str] = None) -> Optional[str]:
    """带彩色提示的输入函数。

    Args:
        prompt: 提示文字
        default: 用户直接回车时采用的默认值

    Returns:
        用户输入，或默认值（未提供默认值时可能为 None）
    """
    default_text = f" [{default}]" if default else ""
    user_input = input(f"{Fore.BLUE}{prompt}{default_text}: {Style.RESET_ALL}")
    return user_input if user_input else default


def get_yes_no(prompt: str, default: bool = True) -> bool:
    """询问一个是 / 否问题。

    Args:
        prompt: 提示文字
        default: 用户直接回车时的默认答案

    Returns:
        bool: 选是返回 True
    """
    default_value = "Y/n" if default else "y/N"
    response = get_user_input(f"{prompt} {default_value}")

    if not response:
        return default

    return response.lower() in ("y", "yes")


def display_summary(report: Dict[str, Any]) -> None:
    """在终端打印评测结果摘要。

    成功率按颜色分级：>80% 绿、>50% 黄、其余红，方便一眼看出这次跑得好不好。

    Args:
        report: 评测报告字典
    """
    print_title("Evaluation Summary")

    print(f"{Fore.CYAN}Model:{Style.RESET_ALL} {report['model']}")
    print(f"{Fore.CYAN}Duration:{Style.RESET_ALL} {report['duration_seconds']} seconds")
    print(f"{Fore.CYAN}Total Traces:{Style.RESET_ALL} {report['total_traces']}")

    success_rate = 0
    if report["total_traces"] > 0:
        success_rate = (report["successful_traces"] / report["total_traces"]) * 100

    if success_rate > 80:
        status_color = Fore.GREEN
    elif success_rate > 50:
        status_color = Fore.YELLOW
    else:
        status_color = Fore.RED

    print(
        f"{Fore.CYAN}Success Rate:{Style.RESET_ALL} {status_color}{success_rate:.1f}%{Style.RESET_ALL} ({report['successful_traces']}/{report['total_traces']})"
    )

    print("\n" + f"{Fore.CYAN}Metrics Summary:{Style.RESET_ALL}")
    for metric_name, data in report["metrics_summary"].items():
        total = data["success_count"] + data["failure_count"]
        success_percent = 0
        if total > 0:
            success_percent = (data["success_count"] / total) * 100

        if success_percent > 80:
            status_color = Fore.GREEN
        elif success_percent > 50:
            status_color = Fore.YELLOW
        else:
            status_color = Fore.RED

        print(
            f"  • {metric_name}: {status_color}{success_percent:.1f}%{Style.RESET_ALL} success, avg score: {data['avg_score']:.2f}"
        )

    if report["generate_report_path"]:
        print(f"\n{Fore.CYAN}Report generated at:{Style.RESET_ALL} {report['generate_report_path']}")


async def run_evaluation(generate_report: bool = True) -> None:
    """执行一次评测。

    Args:
        generate_report: 是否额外输出 JSON 报告文件
    """
    print_title("Starting Evaluation")
    print_info(f"Using model: {settings.EVALUATION_LLM}")
    print_info(f"Report generation: {'Enabled' if generate_report else 'Disabled'}")

    try:
        evaluator = Evaluator()
        await evaluator.run(generate_report_file=generate_report)

        print_success("Evaluation completed successfully!")

        # 跑完立刻打印摘要
        display_summary(evaluator.report)

    except Exception as e:
        print_error(f"Evaluation failed: {str(e)}")
        logger.error("evaluation_failed", error=str(e))
        sys.exit(1)


def display_configuration(config: Dict[str, Any]) -> None:
    """打印当前生效的评测配置。

    Args:
        config: 配置字典
    """
    print_title("Configuration")
    print_info(f"Model: {config['model']}")
    print_info(f"API Base: {config['api_base']}")
    print_info(f"Generate Report: {'Yes' if config['generate_report'] else 'No'}")


def interactive_mode() -> None:
    """交互式引导：逐项确认配置后再开跑。"""
    colorama.init()

    # 以默认值为起点
    config = DEFAULT_CONFIG.copy()

    print_title("Evaluation Runner")
    print_info("Welcome to the Evaluation Runner!")
    print_info("Press Enter to accept default values or input your own.")

    display_configuration(config)

    print("\n" + f"{Fore.CYAN}Configuration Options (press Enter to accept defaults):{Style.RESET_ALL}")

    # 允许逐项修改，也可以全部用默认值
    change_config = get_yes_no("Would you like to change the default configuration?", default=False)

    if change_config:
        config["generate_report"] = get_yes_no("Generate JSON report?", default=config["generate_report"])

    print("\n")
    confirm = get_yes_no("Ready to start evaluation with these settings?", default=True)

    if confirm:
        asyncio.run(run_evaluation(generate_report=config["generate_report"]))
    else:
        print_warning("Evaluation canceled.")


def quick_mode() -> None:
    """全默认配置直接开跑，不做任何交互（对应 ``make eval-quick``）。"""
    colorama.init()
    print_title("Quick Evaluation")
    print_info("Running evaluation with default settings...")
    print_info("(Press Ctrl+C to cancel)")

    display_configuration(DEFAULT_CONFIG)

    try:
        asyncio.run(run_evaluation(generate_report=DEFAULT_CONFIG["generate_report"]))
    except KeyboardInterrupt:
        # Ctrl+C 属于正常的主动取消，退出码 0
        print_warning("\nEvaluation canceled by user.")
        sys.exit(0)


def main() -> None:
    """命令行入口：按参数分发到三种运行模式。"""
    parser = argparse.ArgumentParser(description="Run evaluations on model outputs")
    parser.add_argument("--no-report", action="store_true", help="Don't generate a JSON report")
    parser.add_argument("--interactive", action="store_true", help="Run in interactive mode")
    parser.add_argument("--quick", action="store_true", help="Run with all default settings (no prompts)")

    args = parser.parse_args()

    if args.quick:
        quick_mode()
    elif args.interactive:
        interactive_mode()
    else:
        # 直接按命令行参数跑（默认生成报告）
        asyncio.run(run_evaluation(generate_report=not args.no_report))


if __name__ == "__main__":
    main()
