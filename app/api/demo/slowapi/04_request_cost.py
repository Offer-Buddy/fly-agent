r"""示例 04 · 请求成本 cost（教程 5.2）。

「次数」限流对耗时差异大的接口不公平：一次 ``/chat`` 可能串十几次 LLM 调用，
一次 ``/ping`` 只是查个字典。``cost`` 让请求按重量扣配额。

- 静态成本：``cost=10``，一次请求扣 10 个单位；
- 动态成本：传一个 ``(request) -> int`` 的函数，按参数算成本。

运行：
    uv run python -m app.api.demo.slowapi.04_request_cost

验证（额度 50/minute，cost=10 → 实际只能打 5 次）：
    for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8004/expensive; done
    # 动态成本：complexity=1 扣 2，complexity=5 扣 10
    curl -s "http://127.0.0.1:8004/dynamic-cost?complexity=5"
"""

import uvicorn
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="slowapi demo 04 · 请求成本")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]


def dynamic_cost(request: Request) -> int:
    """按请求复杂度算成本。

    Args:
        request: 当前请求，``complexity`` 查询参数表示复杂度。

    Returns:
        int: 本次请求应扣的配额单位。
    """
    complexity = int(request.query_params.get("complexity", "1"))
    return complexity * 2


@app.get("/expensive")
@limiter.limit("50/minute", cost=10)
async def expensive_route(request: Request) -> dict[str, str]:
    """静态成本：一次请求扣 10 个单位。"""
    return {"result": "costly operation", "charged": "10"}


@app.get("/dynamic-cost")
@limiter.limit("100/minute", cost=dynamic_cost)
async def dynamic_cost_route(request: Request) -> dict[str, str]:
    """动态成本：复杂度 × 2。"""
    complexity = request.query_params.get("complexity", "1")
    return {"complexity": complexity, "charged": str(int(complexity) * 2)}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8004)
