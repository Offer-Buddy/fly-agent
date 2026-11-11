r"""示例 01 · 最小可用集成（教程 3.1）。

要点：
1. ``Limiter`` 只建一次，作为模块级单例复用；
2. 视图函数必须显式声明 ``request: Request``，否则限流无法生效；
3. 路由装饰器写在限流装饰器**之上**，顺序颠倒会报错。

运行：
    uv run python -m app.api.demo.slowapi.01_minimal

验证（连打 6 次，第 6 次应返回 429）：
    for i in $(seq 1 6); do curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/home; done
"""

import uvicorn
from fastapi import FastAPI, Request
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

# 限流器单例：默认按客户端 IP 计数
limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="slowapi demo 01 · 最小可用")
app.state.limiter = limiter
# 官方 429 处理器；pyright 对 add_exception_handler 的第二个参数类型标注偏严
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # pyright: ignore[reportArgumentType]


@app.get("/home")
@limiter.limit("5/minute")
async def home(request: Request) -> dict[str, str]:
    """每个 IP 每分钟最多 5 次请求。"""
    return {"message": "欢迎访问首页！"}


if __name__ == "__main__":
    # 独立运行入口：端口错开主应用的 8000，方便多个 demo 同时起
    uvicorn.run(app, host="127.0.0.1", port=8001)
