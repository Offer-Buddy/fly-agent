"""所有接口响应模型的公共基类。"""

from uuid import UUID, uuid4

from asgi_correlation_id import correlation_id
from pydantic import BaseModel, Field


def _get_request_id() -> UUID:
    """取当前请求的 correlation id；取不到时现生成一个 UUID 兜底。

    Returns:
        UUID: 请求 ID
    """
    value = correlation_id.get()
    return UUID(value) if value else uuid4()


class BaseResponse(BaseModel):
    """响应基类，所有接口响应都继承它。

    ``request_id`` 由 CorrelationIdMiddleware 写入的 ContextVar 自动填充，
    各接口不需要显式传参；排查问题时拿它去日志里搜即可串起整条链路。
    """

    request_id: UUID = Field(default_factory=_get_request_id, description="本次请求的唯一标识")
