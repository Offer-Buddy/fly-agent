# JWT 鉴权：哪些接口加、哪些不加、怎么控制

一句话：**FastAPI 没有「全局鉴权开关」**。保护范围只由一件事决定——依赖挂在哪一层。

本目录是一个独立可跑的最小工程（不接数据库、不接主应用），把这件事拆成四种位置、三档强度、两个反例。

---

## 一、四种挂载位置

| 位置 | 写法 | 保护范围 | 本仓库用没用 |
|---|---|---|---|
| 端点级 | `async def f(u = Depends(require_user))` | 只有这一个接口 | ✅ `app/api/v1/auth.py` |
| 路由级 | `APIRouter(dependencies=[Depends(...)])` | 这一组全部接口 | ✅ `app/api/v1/chatbot.py` |
| 挂载级 | `include_router(r, dependencies=[...])` | 被挂载的那一整块 | ❌ 没用 |
| 中间件/全局 | `add_middleware` 或全局依赖 | 全站 | ❌ 没用，也不该用 |

为什么不用全局：`/register`、`/login`、`/health` 必须在「还没有身份」时被调用，全局拦会把它们一起挡掉。**登录接口的存在，本身就否定了全局开关。**

## 二、三档强度

鉴权不是开关，是刻度。同样一个应用里同时存在三种接口：

| 档位 | 依赖 | 行为 | 典型接口 |
|---|---|---|---|
| 公开 | 不挂 | 谁都能调 | 登录、注册、探活、验证码 |
| 可选 | `optional_user` | 验，但失败放行 | 公开分享页、首页推荐 |
| 必需 | `require_user` / `require_session` | 失败即拒 | 会话管理、聊天 |

**可选档只适合读接口。** 它把「令牌过期」静默降级成匿名，客户端永远发现不了自己该重新登录；用在写接口上就会变成「以为在改自己的数据，其实改的是空用户」。

## 三、判断标准

只需要一句话：**这个接口是不是在拿到身份之前被调用？**

- 是 → 公开（登录、注册、探活、`/docs`）
- 否，且读数据 → 可以半挂（可选）
- 否 → 必需

再补一条容易被忽略的：**签发下级凭证的接口，一定挂上级凭证的依赖**。`POST /auth/session` 用用户令牌换会话令牌，它自己就必须要求用户令牌——否则谁都能凭空造一个会话。

## 四、本仓库的 12 个接口分别在哪一档

跑 `python -m app.api.demo.jwt.audit` 得到，不是我手写的清单：

```
GET     /demo/jwt/ping              公开（白名单）
POST    /demo/jwt/login             公开（白名单）
GET     /demo/jwt/feed              可选
GET     /demo/jwt/me                用户令牌
GET     /demo/jwt/sessions          用户令牌
POST    /demo/jwt/session-token     用户令牌
POST    /demo/jwt/chat              会话令牌
GET     /demo/jwt/messages          会话令牌
DELETE  /demo/jwt/messages          会话令牌
GET     /demo/jwt/cannot-cancel     用户令牌
GET     /demo/jwt/user-secrets      !! 漏挂
GET     /demo/jwt/reports           用户令牌
```

## 五、实测：路由级依赖取消不掉

最容易踩的坑——以为在端点上写 `dependencies=[]` 就能把路由级的依赖摘掉：

```python
trap_router = APIRouter(dependencies=[Depends(require_user)])


@trap_router.get("/cannot-cancel", dependencies=[])  # 这一句无效
async def cannot_cancel(): ...
```

实际请求（`verify.py` 的输出）：

```
端点想取消路由级依赖      GET /cannot-cancel -> 401 Not authenticated
带上用户令牌才进得去      GET /cannot-cancel -> 200
```

装饰器里的 `dependencies` 只能**追加**，不能覆盖上一层。**想让某个接口公开，唯一的办法是把它放进另一个 router。**

## 六、漏挂怎么发现

`forgot_router` 里的 `/user-secrets` 没挂依赖，返回 200，泄露邮箱——**不报错、不告警、测试不覆盖就永远发现不了**。

靠人眼 review 不可靠，所以有 `audit.py`：扫描全部路由，把每个接口的鉴权档位打出来。它做两件事：

1. 从 `Dependant` 树里递归找守卫函数，所以端点级 / 路由级 / 挂载级三种位置都能识别
2. 维护一份 `PUBLIC_ALLOWLIST`（明确豁免的路径）。**没挂依赖、又不在白名单里 → 判为漏挂**

接进 CI 只需一行，有漏挂就以退出码 1 结束：

```bash
python -m app.api.demo.jwt.audit --strict
```

这条路子的价值：把「哪些接口不需要鉴权」从口头约定变成代码里的常量。新增接口时忘了挂依赖，CI 会拦住；真要公开，就得显式往白名单里加一行，也就等于当场review 了一次。

## 七、跑起来

```bash
# 只看鉴权清单
python -m app.api.demo.jwt.audit

# 起服务，打开 http://127.0.0.1:8100/docs 逐条点
python -m app.api.demo.jwt.app
```

`/docs` 里每个接口右侧有个锁图标——**有锁的就是挂了鉴权依赖的**，这是肉眼核对的最快方式。拿令牌的路径：先 `POST /demo/jwt/login` 拿用户令牌，再用它 `POST /demo/jwt/session-token` 换会话令牌。

## 八、与生产实现的差异

| 差异 | demo | 生产 | 为什么 |
|---|---|---|---|
| 数据源 | 内存字典 | PostgreSQL | 让 demo 不依赖数据库就能跑 |
| `HTTPBearer` | `auto_error=False` | 默认 `True` | 默认实现在「没带令牌」时抛 **403**，语义是「你没权限」而不是「你没登录」；demo 统一成 401 |
| `request.state.user_id` | 依赖里写入，中间件读取 | **依赖从未写入** | 生产 `middleware.py` 读了 `request.state.user_id`，但全仓库没有任何地方写它——那段 `hasattr` 永远为 False，属于死代码 |

其余（`verify_token` 校验、401/422/404 三分、令牌里 `sub` 的两种含义）与生产完全一致。
