"""评测流程用到的辅助函数：消息格式化、报告结构维护、结果落盘。"""

import json
import os
from datetime import datetime
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Tuple,
)

from langfuse.api.resources.commons.types.trace_with_details import TraceWithDetails

from app.core.logging import logger
from evals.schemas import ScoreSchema


def format_messages(messages: list[dict]) -> str:
    """把消息列表格式化成便于评测模型阅读的纯文本。

    工具消息会特殊处理：把调用参数与返回内容拼在一起，方便评测模型
    判断「工具用得对不对」。

    Args:
        messages: 消息字典列表

    Returns:
        str: 格式化后的文本，每条消息一行
    """
    formatted_messages = []
    for idx, message in enumerate(messages):
        if message["type"] == "tool":
            # 工具返回本身不带调用参数，需要回看上一条消息里的调用信息
            previous_message = messages[idx - 1]
            tool_call = previous_message.get("additional_kwargs", {}).get("tool_calls", [])
            if tool_call:
                args = tool_call[0].get("function", {}).get("arguments")
            else:
                prev_tool_calls = previous_message.get("tool_calls")
                args = prev_tool_calls[0].get("args") if prev_tool_calls else {}
            content = message.get("content") or ""
            formatted_messages.append(
                f"tool {message.get('name')} input: {args} {content[:100]}..."
                if len(content) > 100
                else f"tool {message.get('name')}: {content}"
            )
        elif message["content"]:
            formatted_messages.append(f"{message['type']}: {message['content']}")
    return "\n".join(formatted_messages)


def get_input_output(trace: TraceWithDetails) -> Tuple[Optional[str], Optional[str]]:
    """从 trace 中拆出「输入消息」与「最终输出消息」。

    约定是：``output.messages`` 的最后一条是模型输出，前面都是输入上下文。

    Args:
        trace: 待处理的 trace

    Returns:
        Tuple[Optional[str], Optional[str]]: (格式化输入, 格式化输出)；
        ``trace.output`` 不是字典时返回 (None, None)
    """
    if not isinstance(trace.output, dict):
        return None, None
    input_messages = trace.output.get("messages", [])[:-1]
    output_message = trace.output.get("messages", [])[-1]
    return format_messages(input_messages), format_messages([output_message])


def initialize_report(model_name: str) -> Dict[str, Any]:
    """初始化报告数据结构。

    Args:
        model_name: 被评测的模型名

    Returns:
        Dict[str, Any]: 空白报告
    """
    return {
        "timestamp": datetime.now().isoformat(),
        "model": model_name,
        "total_traces": 0,
        "successful_traces": 0,
        "failed_traces": 0,
        "duration_seconds": 0,
        "metrics_summary": {},
        "successful_traces_details": [],
        "failed_traces_details": [],
    }


def initialize_metrics_summary(report: Dict[str, Any], metrics: List[Dict[str, str]]) -> None:
    """在报告里预建每个指标的统计槽位。

    Args:
        report: 报告字典
        metrics: 指标定义列表
    """
    for metric in metrics:
        report["metrics_summary"][metric["name"]] = {"success_count": 0, "failure_count": 0, "avg_score": 0.0}


def update_success_metrics(
    report: Dict[str, Any], trace_id: str, metric_name: str, score: ScoreSchema, trace_results: Dict[str, Any]
) -> None:
    """记录一次成功的指标评测。

    注意 ``avg_score`` 这里先做累加，最后由 ``calculate_avg_scores`` 统一除。

    Args:
        report: 报告字典
        trace_id: 当前 trace 的 ID
        metric_name: 指标名
        score: 评测分数
        trace_results: 单条 trace 的结果容器
    """
    trace_results[trace_id]["metrics_succeeded"] += 1
    trace_results[trace_id]["metrics_results"][metric_name] = {
        "success": True,
        "score": score.score,
        "reasoning": score.reasoning,
    }
    report["metrics_summary"][metric_name]["success_count"] += 1
    report["metrics_summary"][metric_name]["avg_score"] += score.score


def update_failure_metrics(
    report: Dict[str, Any], trace_id: str, metric_name: str, trace_results: Dict[str, Any]
) -> None:
    """记录一次失败的指标评测。

    Args:
        report: 报告字典
        trace_id: 当前 trace 的 ID
        metric_name: 指标名
        trace_results: 单条 trace 的结果容器
    """
    trace_results[trace_id]["metrics_results"][metric_name] = {"success": False}
    report["metrics_summary"][metric_name]["failure_count"] += 1


def process_trace_results(
    report: Dict[str, Any], trace_id: str, trace_results: Dict[str, Any], metrics_count: int
) -> None:
    """汇总单条 trace 的结果：全部指标都成功才算这条 trace 成功。

    Args:
        report: 报告字典
        trace_id: 当前 trace 的 ID
        trace_results: 单条 trace 的结果容器
        metrics_count: 指标总数
    """
    if trace_results[trace_id]["metrics_succeeded"] == metrics_count:
        trace_results[trace_id]["success"] = True
        report["successful_traces"] += 1
        report["successful_traces_details"].append(
            {"trace_id": trace_id, "metrics_results": trace_results[trace_id]["metrics_results"]}
        )
    else:
        report["failed_traces"] += 1
        report["failed_traces_details"].append(
            {
                "trace_id": trace_id,
                "metrics_evaluated": trace_results[trace_id]["metrics_evaluated"],
                "metrics_succeeded": trace_results[trace_id]["metrics_succeeded"],
                "metrics_results": trace_results[trace_id]["metrics_results"],
            }
        )


def calculate_avg_scores(report: Dict[str, Any]) -> None:
    """把累加的分数除以成功次数，得到每个指标的平均分。

    Args:
        report: 报告字典
    """
    for _, data in report["metrics_summary"].items():
        if data["success_count"] > 0:
            data["avg_score"] = round(data["avg_score"] / data["success_count"], 2)


def generate_report(report: Dict[str, Any]) -> str:
    """把报告写成 JSON 文件。

    Args:
        report: 报告字典

    Returns:
        str: 生成的文件路径
    """
    report_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reports")
    os.makedirs(report_dir, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = os.path.join(report_dir, f"evaluation_report_{timestamp}.json")

    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)

    # 把路径写回报告，方便调用方直接打印
    report["generate_report_path"] = report_path

    logger.info("evaluation_report_generated", report_path=report_path)
    return report_path
