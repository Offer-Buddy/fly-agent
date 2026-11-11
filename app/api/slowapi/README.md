# slowapi 限流示例（可独立运行）

`app/api/slowapi/` 下每个文件都是一个**自带 FastAPI 实例的独立 demo**，不参与
`app.main` 的路由挂载，也不 import `app.core.*`（免得跑个 demo 还要先配 JWT、
数据库、Redis）。它们对应 `E:/知识课程/0805/slowapi_guide.md` 的各个章节。

**编号规则**：`01`–`09` 就是演示顺序，也正好等于端口尾号（`01` → 8001，
`02` → 8002……）。按文件名排序 = 按教程顺序讲。

## 一览

| 文件 | 教程章节 | 端口 | 演示什么 |
| --- | --- | --- | --- |
| `01_minimal.py` | 3.1 | 8001 | 最小可用：单路由 `@limiter.limit` |
| `02_global_limits.py` | 4 | 8002 | `default_limits` + `SlowAPIMiddleware` + `exempt` |
| `03_dynamic_limit.py` | 5.1 | 8003 | 额度函数按 `key` 前缀给额度（形参名必须是 `key`） |
| `04_request_cost.py` | 5.2 | 8004 | 静态 `cost=10` 与动态 cost 函数 |
| `05_custom_key.py` | 5.3 | 8005 | 全局键（IP + 套餐）/ 单路由覆盖键（用户 ID） |
| `06_key_style.py` | 5.4 | 8006 | `url`（按路径）与 `endpoint`（按函数名）对照 |
| `07_toggle.py` | 5.5 | 8007 | 运行时 `limiter.enabled` 开关 |
| `08_storage_backend.py` | 6 | 8008 | 内存 / Redis / `strategy` 切换 |
| `09_full_example.py` | 附录 + 9 | 8009 | 全套组合 + 自定义 429 + structlog 日志 |

## 运行

```bash
# 依赖先对齐（不需要额外装，slowapi 已在 pyproject 里）
uv sync

# 起第 n 个：模块名 = 文件名去掉 .py
uv run python -m app.api.slowapi.01_minimal
uv run python -m app.api.slowapi.09_full_example
```

演示时按 01 → 09 顺序逐个起（每个占一个端口，可以同时开着，切换只需换浏览器标签）：

```bash
uv run python -m app.api.slowapi.01_minimal        # 8001
uv run python -m app.api.slowapi.02_global_limits  # 8002
uv run python -m app.api.slowapi.03_dynamic_limit  # 8003
uv run python -m app.api.slowapi.04_request_cost   # 8004
uv run python -m app.api.slowapi.05_custom_key     # 8005
uv run python -m app.api.slowapi.06_key_style      # 8006
uv run python -m app.api.slowapi.07_toggle         # 8007
uv run python -m app.api.slowapi.08_storage_backend # 8008
uv run python -m app.api.slowapi.09_full_example   # 8009
```

**必须用 `-m`，不要 `uv run python app/api/slowapi/01_minimal.py`**——后者会把
`app/api/slowapi/` 当成 `sys.path[0]`，`from app...` 直接 ModuleNotFoundError。

要热重载就换个起法（模块路径同样从项目根算）：

```bash
uv run uvicorn app.api.slowapi.01_minimal:app --reload --port 8001
```

> 文件名带数字前缀不影响 `-m` 与 `uvicorn 模块:属性` 两种起法（内部都走
> `importlib`，不要求模块名是合法标识符）。只有 `from ... import` 语句写不了
> 数字开头的模块名——demo 之间互不 import，所以无所谓。

## 验证

限流这种东西「跑起来没报错」等于没验证，得看到 429：

```bash
# 连打 6 次，第 6 次是 429
for i in $(seq 1 6); do
  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8001/home
done

# 看 429 的响应体
curl -s http://127.0.0.1:8001/home
# {"error":"Rate limit exceeded: 5 per 1 minute"}
```

想顺一遍全部 demo，按序号打各自端口即可——每个文件顶部 docstring 里写了该 demo
专属的验证命令（哪个接口打几次、第几次该 429），照抄就能复现。

想看 `X-RateLimit-*` / `Retry-After` 响应头得额外开：`Limiter(headers_enabled=True)`，
并且视图签名里要带 `response: Response`（缺了会抛
`parameter 'response' must be an instance of starlette.responses.Response`）。
demo 为了跟教程的「最小」保持一致没开这个开关。

## 几个容易踩的点

- **`request: Request` 必须出现在函数签名里**，缺了限流不生效（`exempt` 路由除外）。
- **`default_limits` 必须有 `SlowAPIMiddleware`**，否则只是摆设——主项目
  `app/core/limiter.py` 目前就处于这个状态，见教程第 9 节末条。
- **动态额度的函数形参必须叫 `key`**：`@limiter.limit(fn)` 里 `fn(request)` 会
  `TypeError`，因为 slowapi 传进去的是 `key_func` 的返回值（见 `03_dynamic_limit.py`）。
- **`key_style` 不看 query 参数**：`/list?page=1` 与 `?page=2` 同一个桶；
  它区分的是**路径**（`url`）还是**视图函数名**（`endpoint`），见 `06_key_style.py`。
- **挂了中间件后 `/docs` 也吃全局额度**，额度设小会出现「一开文档页就 429」，
  演示时用 `curl`，别先开浏览器。
- **内存后端多进程各自计数**：`uvicorn --workers 4` 下实际阈值 ×4，
  只有 Redis 后端才是全实例统一。

## 和主项目的关系

这些 demo 是「教学/对照」用的，主项目真正的落地在：

- `app/core/config.py` — `RATE_LIMIT_DEFAULT`、`RATE_LIMIT_ENDPOINTS`
- `app/core/limiter.py` — `limiter` 单例，有 Redis 用 Redis，没有退内存
- `app/main.py` — `app.state.limiter` + `RateLimitExceeded` 处理器
- `app/api/v1/*.py` — 每个路由挂 `@limiter.limit(settings.RATE_LIMIT_ENDPOINTS[...][0])`

改主项目时别照抄 demo 的写法（demo 为了自洽刻意不依赖 settings）。
