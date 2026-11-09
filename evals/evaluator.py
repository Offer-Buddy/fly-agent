"""评测器：拉取 Langfuse 的 trace，逐条按指标打分，再把分数写回 Langfuse。"""

import os
import sys
import time
from datetime import (
    datetime,
    timedelta,
)
from time import sleep

import openai
from langfuse import Langfuse
from langfuse.api.resources.commons.types.trace_with_details import TraceWithDetails
from tqdm import tqdm

# 让脚本能直接 import 到项目内的 app 包
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.config import settings
from app.core.logging import logger
from evals.helpers import (
    calculate_avg_scores,
    generate_report,
    get_input_output,
    initialize_metrics_summary,
    initialize_report,
    process_trace_results,
    update_failure_metrics,
    update_success_metrics,
)
from evals.metrics import metrics
from evals.schemas import ScoreSchema


class Evaluator:
    """用预定义指标评测模型输出。

    职责三段：从 Langfuse 拉 trace → 调评测模型逐指标打分 →
    把分数回写到同一条 trace 上（便于在 Langfuse 界面里筛选与对比）。

    Attributes:
        client: OpenAI 兼容客户端，用于调用评测模型
        langfuse: Langfuse 客户端，用于读取 trace 与回写分数
    """

    def __init__(self):
        """初始化 OpenAI 与 Langfuse 客户端，并准备报告结构。"""
        self.client = openai.AsyncOpenAI(api_key=settings.EVALUATION_API_KEY, base_url=settings.EVALUATION_BASE_URL)
        self.langfuse = Langfuse(
            public_key=settings.LANGFUSE_PUBLIC_KEY,
            secret_key=settings.LANGFUSE_SECRET_KEY,
            timeout=60,  # 单位：秒
        )
        # 报告骨架：先建好，过程中不断填充
        self.report = initialize_report(settings.EVALUATION_LLM)
        initialize_metrics_summary(self.report, metrics)

    async def run(self, generate_report_file=True):
        """主流程：拉取 trace、逐条评测、回写分数。

        Args:
            generate_report_file: 是否在结束后输出 JSON 报告，默认 True
        """
        start_time = time.time()
        traces = self.__fetch_traces()
        self.report["total_traces"] = len(traces)

        trace_results = {}

        for trace in tqdm(traces, desc="Evaluating traces"):
            trace_id = trace.id
            trace_results[trace_id] = {
                "success": False,
                "metrics_evaluated": 0,
                "metrics_succeeded": 0,
                "metrics_results": {},
            }

            for metric in tqdm(metrics, desc=f"Applying metrics to trace {trace_id[:8]}...", leave=False):
                metric_name = metric["name"]
                input, output = get_input_output(trace)
                if input is None or output is None:
                    # trace 里取不到输入或输出，这条指标直接记失败
                    update_failure_metrics(self.report, trace_id, metric_name, trace_results)
                    trace_results[trace_id]["metrics_evaluated"] += 1
                    continue
                score = await self._run_metric_evaluation(metric, input, output)

                if score:
                    self._push_to_langfuse(trace, score, metric)
                    update_success_metrics(self.report, trace_id, metric_name, score, trace_results)
                else:
                    update_failure_metrics(self.report, trace_id, metric_name, trace_results)

                trace_results[trace_id]["metrics_evaluated"] += 1

            process_trace_results(self.report, trace_id, trace_results, len(metrics))
            # 每条 trace 之间停一下，避免把评测模型打到限流
            sleep(settings.EVALUATION_SLEEP_TIME)

        self.report["duration_seconds"] = round(time.time() - start_time, 2)
        calculate_avg_scores(self.report)

        if generate_report_file:
            generate_report(self.report)

        logger.info(
            "evaluation_completed",
            total_traces=self.report["total_traces"],
            successful_traces=self.report["successful_traces"],
            failed_traces=self.report["failed_traces"],
            duration_seconds=self.report["duration_seconds"],
        )

    def _push_to_langfuse(self, trace: TraceWithDetails, score: ScoreSchema, metric: dict):
        """把一条评分写回 Langfuse。

        Args:
            trace: 被打分的 trace
            score: 评测得到的分数与理由
            metric: 产生该分数的指标定义
        """
        self.langfuse.create_score(
            trace_id=trace.id,
            name=metric["name"],
            data_type="NUMERIC",
            value=score.score,
            comment=score.reasoning,
        )

    async def _run_metric_evaluation(self, metric: dict, input: str, output: str) -> ScoreSchema | None:
        """用单个指标评测一条 trace。

        Args:
            metric: 指标定义（含 name 与 prompt）
            input: 待评测的输入
            output: 待评测的输出

        Returns:
            评测成功返回 ScoreSchema，失败返回 None
        """
        metric_name = metric["name"]
        if not metric:
            logger.error("metric_not_found", metric_name=metric_name)
            return None
        system_metric_prompt = metric["prompt"]

        if not input or not output:
            logger.error(
                "metric_evaluation_failed_missing_io",
                metric_name=metric_name,
                has_input=bool(input),
                has_output=bool(output),
            )
            return None
        score = await self._call_openai(system_metric_prompt, input, output)
        if score:
            logger.info(
                "metric_evaluation_completed",
                metric_name=metric_name,
                score=score.score,
                reasoning=score.reasoning,
            )
        else:
            logger.error("metric_evaluation_failed", metric_name=metric_name)
        return score

    async def _call_openai(self, metric_system_prompt: str, input: str, output: str) -> ScoreSchema | None:
        """调用评测模型给单个指标打分。

        失败会重试 3 次，每次间隔 10 秒。

        Args:
            metric_system_prompt: 该指标的系统提示词
            input: 格式化后的输入
            output: 格式化后的输出

        Returns:
            评测成功返回 ScoreSchema，三次都失败返回 None
        """
        num_retries = 3
        for _ in range(num_retries):
            try:
                response = await self.client.beta.chat.completions.parse(
                    model=settings.EVALUATION_LLM,
                    messages=[
                        {"role": "system", "content": metric_system_prompt},
                        {"role": "user", "content": f"Input: {input}\nGeneration: {output}"},
                    ],
                    response_format=ScoreSchema,
                )
                return response.choices[0].message.parsed
            except Exception as e:
                SLEEP_TIME = 10
                logger.error(
                    "openai_evaluation_call_failed",
                    error=str(e),
                    sleep_seconds=SLEEP_TIME,
                )
                sleep(SLEEP_TIME)
                continue
        return None

    def __fetch_traces(self) -> list[TraceWithDetails]:
        """拉取最近 24 小时内、尚未打分的 trace。

        已打分的会被跳过，避免重复消耗评测额度。

        Returns:
            list[TraceWithDetails]: 待评测的 trace 列表；拉取失败返回空列表
        """
        last_24_hours = datetime.now() - timedelta(hours=24)
        logger.info("fetching_langfuse_traces", from_timestamp=str(last_24_hours))
        try:
            traces = self.langfuse.api.trace.list(
                from_timestamp=last_24_hours, order_by="timestamp.asc", limit=100
            ).data
            traces_without_scores = [trace for trace in traces if not trace.scores]
            return traces_without_scores
        except Exception as e:
            logger.error("langfuse_traces_fetch_failed", error=str(e))
            return []
