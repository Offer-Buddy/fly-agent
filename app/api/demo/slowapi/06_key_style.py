r"""示例 06 · 限流键风格 key_style（教程 5.4）。

``key_style`` 决定「路径」怎么参与计数，只有两种取值：

- ``"url"``（默认）：用**实际请求路径** ``request["path"]`` 做 scope，
  所以 ``/url/1`` 与 ``/url/2`` 是两个桶；
- ``"endpoint"``：用**视图函数全名** ``module.func`` 做 scope，
  同一个函数处理的所有路径参数合并成一个桶。

两个必须记住的事实（教程原稿这里写错过）：

1. **query 参数不参与**——scope 取的是 ``request["path"]``，不含 ``?page=1``，
   所以 ``/list?page=1`` 与 ``/list?page=2`` 在两种风格下都是同一个桶。
   要按参数分开计数，只能自己写 ``key_func`` 把参数编进 key。
2. 该参数是 ``Limiter`` 构造参数，**不能在单个路由上覆盖**，所以这里建了两个实例。

运行：
    uv run python -m app.api.demo.slowapi.06_key_style

验证（额度 3/minute）：
    # url 风格：/url/1 打满不影响 /url/2
    for i in $(seq 1 4); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8006/url/1; done   # 200 200 200 429
    curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8006/url/2                                  # 200

    # endpoint 风格：/endpoint/1 打满后 /endpoint/2 也 429（共享桶）
    for i in $(seq 1 4); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8006/endpoint/1; done
    curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8006/endpoint/2                              # 429
"""

import uvicorn
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter_url = Limiter(key_func=get_remote_address, key_style="url")
limiter_endpoint = Limiter(key_func=get_remote_address, key_style="endpoint")

app = FastAPI(title="slowapi demo 06 · key_style")
app.state.limiter = limiter_url
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]


@app.get("/url/{item_id}")
@limiter_url.limit("3/minute")
async def by_url(item_id: str, request: Request) -> dict[str, str]:
    """键风格 url：不同路径参数各自独立计数。"""
    return {"key_style": "url", "item_id": item_id}


@app.get("/endpoint/{item_id}")
@limiter_endpoint.limit("3/minute")
async def by_endpoint(item_id: str, request: Request) -> dict[str, str]:
    """键风格 endpoint：不同路径参数共享同一份额度。"""
    return {"key_style": "endpoint", "item_id": item_id}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8006)
