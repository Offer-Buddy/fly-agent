"""API v1 路由汇总。

把各业务模块的子路由挂到统一前缀下，新增模块时在这里注册一次即可。
"""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.chatbot import router as chatbot_router
from app.core.logging import logger

api_router = APIRouter()

# 注册子路由（tags 决定 OpenAPI 文档里的分组）
api_router.include_router(auth_router, prefix="/auth", tags=["Auth"])
api_router.include_router(chatbot_router, prefix="/chatbot", tags=["Chatbot"])


@api_router.get("/health")
async def health_check():
    """健康检查接口，供负载均衡与探活使用。

    Returns:
        dict: 健康状态与服务版本
    """
    logger.info("health_check_called")
    return {"status": "healthy", "version": "1.0.0"}
