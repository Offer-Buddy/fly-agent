"""应用入口：创建 FastAPI 实例、注册中间件、管理启动/关闭生命周期。"""

import asyncio
import sys
from contextlib import asynccontextmanager
from datetime import datetime

from dotenv import load_dotenv
import uvicorn

# psycopg 的异步连接无法运行在 Windows 默认的 ProactorEventLoop 上，
# 表现为 LangGraph 检查点连接池的每条连接都超时。这里切到 Selector 事件循环。
# 该设置只在「当前还没有事件循环」时生效，因此对脚本与导入场景有效；
# 在 uvicorn 下是空操作（uvicorn 会先建好自己的循环）。那种情况下请用
# --reload（uvicorn 本身就会选 Selector 循环），或在调用 uvicorn.run() 之前
# 先设置好策略的启动脚本。
if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from fastapi import (
    FastAPI,
    Request,
    status,
)
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from asgi_correlation_id import CorrelationIdMiddleware

from app.api.v1.api import api_router
from app.api.v1.chatbot import agent
from app.core.cache import cache_service
from app.core.config import settings
from app.core.limiter import limiter
from app.core.logging import logger
from app.core.middleware import (
    LoggingContextMiddleware,
    ProfilingMiddleware,
)
from app.core.observability import langfuse_init
from app.services.database import database_service
from app.services.memory import memory_service

# 加载环境变量（配置模块已加载过一次，这里兜底被单独 import 的场景）
load_dotenv()
# 初始化链路追踪（关闭时是空操作）
langfuse_init()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动与关闭时的初始化 / 清理逻辑。

    三个预热步骤都单独 try，任何一个失败都只记日志、不阻断启动：
    缓存与记忆属于增强能力，缺少时可降级运行。
    """
    logger.info(
        "application_startup",
        project_name=settings.PROJECT_NAME,
        version=settings.VERSION,
        api_prefix=settings.API_V1_STR,
    )

    # 初始化缓存（配了 Redis 就建连接，否则用内存缓存）
    try:
        await cache_service.initialize()
    except Exception as e:
        logger.exception("cache_initialization_failed", error=str(e))

    # 预热 LangGraph：提前建好图与连接池，避免第一个请求吃冷启动延迟
    try:
        await agent.create_graph()
        logger.info("graph_pre_warmed")
    except Exception as e:
        logger.exception("graph_pre_warm_failed", error=str(e))

    # 预热 mem0 的 AsyncMemory：提前建 pgvector 连接并核对表结构，
    # 否则首次 search() 未命中缓存或首次 add() 要多付约 130ms 的初始化开销
    try:
        await memory_service.initialize()
    except Exception as e:
        logger.exception("memory_service_pre_warm_failed", error=str(e))

    yield

    # 关闭阶段：先放缓存连接，再关数据库连接池
    await cache_service.close()
    if agent._connection_pool:
        await agent._connection_pool.close()
        logger.info("connection_pool_closed")
    logger.info("application_shutdown")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description=settings.DESCRIPTION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# 日志上下文中间件：要先于其他中间件注册，才能覆盖后续所有日志；
# 它同时负责记每条请求的耗时（http_request_completed）
app.add_middleware(LoggingContextMiddleware)

# 性能剖析中间件：仅 DEBUG 开启，慢请求会把报告写到 PROFILING_DIR
if settings.DEBUG:
    app.add_middleware(ProfilingMiddleware)

# correlation-id 中间件必须最外层，保证 request_id 在其他中间件之前就已生成
app.add_middleware(CorrelationIdMiddleware)

# 限流超限的异常处理
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]


# 请求体校验失败时的异常处理
@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    """把 pydantic 的校验错误整理成对前端友好的结构。

    Args:
        request: 触发校验失败的请求
        exc: 校验异常

    Returns:
        JSONResponse: 统一格式的错误响应
    """
    # 记一条结构化日志，便于排查前端传参问题
    logger.error(
        "validation_error",
        client_host=request.client.host if request.client else "unknown",
        path=request.url.path,
        errors=str(exc.errors()),
    )

    # 把 loc 拼成 "messages -> 0 -> content" 这种可读路径（去掉 body 这一层）
    formatted_errors = []
    for error in exc.errors():
        loc = " -> ".join([str(loc_part) for loc_part in error["loc"] if loc_part != "body"])
        formatted_errors.append({"field": loc, "message": error["msg"]})

    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"detail": "Validation error", "errors": formatted_errors},
    )


# 跨域配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载业务路由
app.include_router(api_router, prefix=settings.API_V1_STR)


@app.get("/")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["root"][0])
async def root(request: Request):
    """根路径，返回服务基本信息与文档入口。"""
    logger.info("root_endpoint_called")
    return {
        "name": settings.PROJECT_NAME,
        "version": settings.VERSION,
        "status": "healthy",
        "environment": settings.ENVIRONMENT.value,
        "swagger_url": "/docs",
        "redoc_url": "/redoc",
    }


@app.get("/health")
@limiter.limit(settings.RATE_LIMIT_ENDPOINTS["health"][0])
async def health_check(request: Request) -> JSONResponse:
    """健康检查，附带依赖组件状态。

    数据库不可达时返回 503，好让负载均衡把这个实例摘掉。

    Returns:
        JSONResponse: 健康状态；数据库异常时状态码为 503
    """
    logger.info("health_check_called")

    # 探测数据库连通性
    db_healthy = await database_service.health_check()

    response = {
        "status": "healthy" if db_healthy else "degraded",
        "version": settings.VERSION,
        "environment": settings.ENVIRONMENT.value,
        "components": {"api": "healthy", "database": "healthy" if db_healthy else "unhealthy"},
        "timestamp": datetime.now().isoformat(),
    }

    # 数据库不健康就返回 503，而不是 200
    status_code = status.HTTP_200_OK if db_healthy else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(content=response, status_code=status_code)


if __name__ == "__main__":
    # 直接运行本文件即可起开发服务器（PyCharm 右键运行 / python app/main.py）。
    #
    # reload=True 不只是为了热重载：Windows 上 uvicorn 只在子进程模式
    # （use_subprocess=True）才会把事件循环切成 SelectorEventLoop，而
    # psycopg 的异步连接在默认的 ProactorEventLoop 上会全部失败。
    # 本模块顶部的 set_event_loop_policy 在这里是空操作（uvicorn 会先建好
    # 自己的循环再 import 本模块），所以必须靠 reload 兜住。
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
