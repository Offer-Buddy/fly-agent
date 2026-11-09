"""自定义中间件：日志上下文与按需性能剖析。

两个中间件的注册顺序在 ``app/main.py`` 里，顺序会影响上下文能否覆盖到
异常路径，改动前先看清楚。

请求级的计数与耗时不再走 Prometheus 指标（已随监控栈一起移除），
改由 ``LoggingContextMiddleware`` 每条请求打一句 ``http_request_completed``，
耗时字段 ``duration_ms`` 就是原来 histogram 观测的那个值。
"""

import json
import time
import tracemalloc
from typing import (
    TYPE_CHECKING,
    Callable,
    override,
)

from asgi_correlation_id import correlation_id
from fastapi import Request
from jose import (
    JWTError,
    jwt,
)
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.config import settings
from app.core.logging import (
    bind_context,
    clear_context,
    logger,
)

# pyinstrument 是开发依赖，生产镜像可能没装；保留探测以便优雅降级
if TYPE_CHECKING:
    from pyinstrument import Profiler  # pyright: ignore[reportMissingImports]
    from pyinstrument.renderers import JSONRenderer  # pyright: ignore[reportMissingImports]

    PYINSTRUMENT_AVAILABLE = True
else:
    try:
        from pyinstrument import Profiler
        from pyinstrument.renderers import JSONRenderer

        PYINSTRUMENT_AVAILABLE = True
    except ImportError:
        Profiler = None
        JSONRenderer = None
        PYINSTRUMENT_AVAILABLE = False


class LoggingContextMiddleware(BaseHTTPMiddleware):
    """把 user_id / session_id 注入日志上下文，并为每条请求记一次耗时。

    耗时替代了原先 Prometheus histogram 的位置：没有指标栈之后，
    ``http_request_completed`` 这条日志是唯一的逐请求耗时来源。
    """

    @override
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """从已鉴权请求中提取 session_id 并绑定到日志上下文。

        Args:
            request: 进来的请求
            call_next: 下一个中间件或路由处理函数

        Returns:
            Response: 应用返回的响应
        """
        # perf_counter 单调递增，不受系统时钟调整影响，适合测时长
        start_time = time.perf_counter()
        # 先预设 500：如果 call_next 抛异常，日志里仍能记下这次失败
        status_code = 500

        try:
            # 清掉上一个请求可能残留的上下文
            clear_context()

            # 从 Authorization 头取 token
            auth_header = request.headers.get("authorization")
            if auth_header and auth_header.startswith("Bearer "):
                token = auth_header.split(" ")[1]

                try:
                    # 解码 token 拿 session_id（放在 "sub" 声明里）
                    payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
                    session_id = payload.get("sub")

                    if session_id:
                        # 绑定 session_id，后续日志自动带上
                        bind_context(session_id=session_id)

                        # user_id 由鉴权依赖写入 request.state，
                        # 这里拿不到，等请求处理完再取

                except JWTError:
                    # token 无效也不在这里拦，交给鉴权依赖去返回 401
                    pass

            # 放行请求
            response = await call_next(request)
            status_code = response.status_code

            # 请求处理完后再补 user_id（鉴权依赖此时已写入 request.state）
            if hasattr(request.state, "user_id"):
                bind_context(user_id=request.state.user_id)

            return response

        finally:
            # 逐请求耗时：替代原 Prometheus histogram，慢请求靠这条日志捞
            logger.info(
                "http_request_completed",
                method=request.method,
                path=request.url.path,
                status_code=status_code,
                duration_ms=round((time.perf_counter() - start_time) * 1000, 2),
            )
            # 无论成功失败都要清空，避免上下文泄漏到下一个请求
            clear_context()


class ProfilingMiddleware(BaseHTTPMiddleware):
    """基于 pyinstrument 的逐请求性能剖析中间件。

    仅在 ``DEBUG=true`` 时挂载。每个请求都会被采样，当耗时超过
    ``PROFILING_THRESHOLD_SECONDS`` 时把 JSON 报告写入 ``PROFILING_DIR``。
    文件名用 request_id，方便与日志对照；``/tmp`` 下的历史文件由操作系统回收。
    """

    @override
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """剖析单个请求，超阈值时落盘报告。"""
        if not PYINSTRUMENT_AVAILABLE:
            return await call_next(request)

        # 同时开三种采样：内存分配追踪 + CPU 时间 + 调用树
        tracemalloc.start()
        cpu_start = time.process_time()

        profiler = Profiler(async_mode="enabled")
        with profiler:
            response = await call_next(request)

        # 请求一结束立刻取数，避免被后续逻辑干扰
        cpu_ms = round((time.process_time() - cpu_start) * 1000, 2)
        mem_current_kb, mem_peak_kb = (v // 1024 for v in tracemalloc.get_traced_memory())
        snapshot = tracemalloc.take_snapshot()
        tracemalloc.stop()

        wall_ms = round((profiler.last_session.duration if profiler.last_session else 0.0) * 1000, 2)

        if wall_ms / 1000 >= settings.PROFILING_THRESHOLD_SECONDS:
            raw_id = correlation_id.get() or "unknown"
            # 把 32 位无连字符的 id 补成标准 UUID 形式，便于肉眼识别
            if len(raw_id) == 32 and "-" not in raw_id:
                raw_id = f"{raw_id[:8]}-{raw_id[8:12]}-{raw_id[12:16]}-{raw_id[16:20]}-{raw_id[20:]}"

            settings.PROFILING_DIR.mkdir(parents=True, exist_ok=True)
            filepath = settings.PROFILING_DIR / f"{raw_id}.json"

            # 内存占用 Top 20，排除剖析器自身与标准库噪声
            _excluded = ("tracemalloc", "pyinstrument", "<frozen", "logging/__init__")
            top_allocs = [
                {
                    "file": str(stat.traceback[0].filename).replace(str(__file__).rsplit("/", 3)[0] + "/", ""),
                    "line": stat.traceback[0].lineno,
                    "size_kb": round(stat.size / 1024, 2),
                    "count": stat.count,
                }
                for stat in snapshot.statistics("lineno")
                if not any(ex in str(stat.traceback[0].filename) for ex in _excluded)
            ]

            call_tree = json.loads(profiler.output(renderer=JSONRenderer()))
            report = {
                "request_id": raw_id,
                "endpoint": f"{request.method} {request.url.path}",
                "wall_time_ms": wall_ms,
                "cpu_time_ms": cpu_ms,
                "io_wait_ms": round(wall_ms - cpu_ms, 2),
                "memory_peak_kb": mem_peak_kb,
                "memory_allocated_kb": mem_current_kb,
                "top_memory_allocators": top_allocs,
                "call_tree": call_tree,
            }
            filepath.write_text(json.dumps(report, indent=2))
            logger.debug(
                "slow_request_profile_saved",
                path=request.url.path,
                method=request.method,
                wall_time_ms=wall_ms,
                cpu_time_ms=cpu_ms,
                memory_peak_kb=mem_peak_kb,
                io_wait_ms=round(wall_ms - cpu_ms, 2),
                profile_file=str(filepath),
            )

        return response
