"""独立 Demo 应用：只挂 demo 路由，不碰主应用。

起法（在仓库根目录）：``python -m app.api.demo.jwt.app``

刻意**不**在 ``app/api/v1/api.py`` 里注册。demo 路由里有一个故意漏挂鉴权的
接口，还有一条泄露邮箱的假数据，绝不能跟着主应用一起上线。
"""

import uvicorn
from fastapi import (
    Depends,
    FastAPI,
)

from app.api.demo.jwt.deps import require_user
from app.api.demo.jwt.routers import (
    forgot_router,
    legacy_router,
    public_router,
    session_router,
    trap_router,
    user_router,
)

app = FastAPI(
    title="JWT 鉴权控制 Demo",
    description=(
        "演示四种挂载位置、三档强度与两个反例。"
        "访问 /docs 可以逐条点开试，注意每个接口右侧的锁图标——"
        "有锁的就是挂了鉴权依赖的。"
    ),
    version="1.0.0",
)

# 统一前缀，避免和主应用路径撞车
PREFIX = "/demo/jwt"

# 第一、二、三组：各自带自己的控制方式
app.include_router(public_router, prefix=PREFIX)
app.include_router(user_router, prefix=PREFIX)
app.include_router(session_router, prefix=PREFIX)

# 两个反例
app.include_router(trap_router, prefix=PREFIX)
app.include_router(forgot_router, prefix=PREFIX)

# 第四种位置：挂载时统一加。
# 适合「模块是别人写的 / 是旧代码，不想改它的源码，但必须补上鉴权」的场景。
app.include_router(legacy_router, prefix=PREFIX, dependencies=[Depends(require_user)])


if __name__ == "__main__":
    # 传实例而不是字符串：字符串形式要求模块可被重新导入，Windows 上还要处理
    # 事件循环策略（主应用的 main.py 里有相关注释），demo 不需要 reload。
    uvicorn.run(app, host="127.0.0.1", port=8100)
