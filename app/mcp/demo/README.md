# mcp/demo：一个后端，两个门

对应课件 `docs/教程/FastAPI与FastMCP_一个后端两个门.html`。
按 ①→⑧ 读，每一步都能单独跑、单独删掉，不碰 `app.main` 的路由挂载。

| 子目录 | 回答什么问题 | 入口命令 |
| --- | --- | --- |
| `01_hello_mcp/` | MCP 到底是什么，最小能小到什么程度 | `python -m app.mcp.demo.01_hello_mcp.client`（stdio，不占端口） |
| `02_task_app/` | 怎么挂进现有 FastAPI，鉴权和限流怎么只写一份 | `python -m app.mcp.demo.02_task_app.main`（8010） |

## 编号总表

| # | 文件 | 干什么 |
| --- | --- | --- |
| ① | `01_hello_mcp/server.py` | 最小 MCP 服务器，15 行，不碰 FastAPI |
| ② | `01_hello_mcp/client.py` | 扮演客户端，把 ① 当子进程拉起来 |
| ③ | `02_task_app/config.py` | 账号、密钥、额度 |
| ④ | `02_task_app/service.py` | 纯业务，不认识任何框架 |
| ⑤ | `02_task_app/security.py` | JWT 签发校验 + 滑动窗口限流 + ASGI 网关 |
| ⑥ | `02_task_app/tools.py` | 把 ④ 包成三个 MCP 工具 |
| ⑦ | `02_task_app/main.py` | 外壳：路由 + 挂载 + 装网关 |
| ⑧ | `02_task_app/mcp_client.py` | 以 AI 身份验收，不参与业务 |

依赖方向是单向的，看箭头就知道谁不能碰谁：

```
⑦ main  ──→ ⑥ tools ──→ ④ service
   │           │            ↑
   └──→ ⑤ security ────────┘     ③ config 被 ⑤⑦ 读
```

④ 一个箭头都不往外指 —— 这就是「业务层不认识框架」的意思。

## 三条约定

1. **生产代码不 import 这里**，这里也不 import `app.main`。只有 `02_task_app/` 内部互相引用。
2. **端口避开 8000**。`02_task_app` 固定 8010，可以和主项目同时开。
3. **目录名带数字前缀**，`01_` → `02_` 就是阅读顺序。用 `python -m` 或 `uvicorn` 启动
   （它们走运行时导入，不受 Python 标识符不能数字开头的影响）；但**别在代码里写
   `from app.mcp.demo.02_task_app import ...`**，那种语句式导入会报语法错误，包内一律用相对导入。

## 前置：两个包

```bash
uv add fastmcp pyjwt
```

主项目依赖里没有它们 —— MCP 在这组示例里是独立场景，不在 Agent 的生产链路上。
跑之前加一次即可。

> 如果正在 IDE 里调试主项目，`.venv` 会被 debugpy 占用，`uv add` 会报
> `os error 5 拒绝访问`。先把调试会话停掉。

## 怎么跑

### 第 1 步：最小 MCP（不需要起服务）

```bash
python -m app.mcp.demo.01_hello_mcp.client
```

预期输出：

```
工具清单： ['add', 'now']
1 + 1   ： 2
现在几点： 2026-11-11T11:49:12
```

### 第 2 步：挂进 FastAPI

```bash
python -m app.mcp.demo.02_task_app.main        # 起服务，8010
```

等价写法：`uvicorn app.mcp.demo.02_task_app.main:app --port 8010`。

另开一个终端，验 AI 通道：

```bash
python -m app.mcp.demo.02_task_app.mcp_client
```

换端口的话，⑧ 那边设 `MCP_BASE` 环境变量跟着改。

## 五个验收点

| # | 做什么 | 应该看到 |
| --- | --- | --- |
| 1 | 启动看日志 | `Application startup complete`；原本有预热的话，那几行也要出现 |
| 2 | `curl -i http://127.0.0.1:8010/api/tasks`（不带票） | `401` + `{"error":"invalid_token"}` |
| 3 | 带 `Authorization: Bearer <token>` 再请求 | `200`，`user` 是你登录的名字 |
| 4 | 连打 8 次（额度 6 次 / 10 秒） | 前 6 次 `200`，第 7 次 `429` + `retry-after` |
| 5 | 跑 ⑧ | 工具三项、`whoami` 认出 alice、建的任务 REST 侧也看得到 |

换票：

```bash
curl -X POST http://127.0.0.1:8010/api/token \
  -H "Content-Type: application/json" \
  -d '{"username":"alice","password":"alice-pwd"}'
```

实测第 5 点长这样：

```
工具清单： ['whoami', 'create_task', 'list_tasks']
我是谁  ： {'username': 'alice'}
新建任务： {'id': 1, 'title': 'alice 的任务', 'owner': 'alice', 'done': False}
我的任务： [{'id': 1, 'title': 'alice 的任务', 'owner': 'alice', 'done': False}]
无 token 调工具 → HTTPStatusError Client error '401 Unauthorized'
```

## 四条容易踩的坑

| 症状 | 原因 | 怎么改 |
| --- | --- | --- |
| 握手 500，日志有 `task group` | 完全没给 lifespan | 用 `async with mcp_app.lifespan(app)` 套住 `yield` |
| MCP 能用，但预热一条没跑 | lifespan 被**替换**了 | 别写 `FastAPI(lifespan=mcp_app.lifespan)` |
| 工具一直报「缺少 Bearer token」 | `get_http_headers()` 默认剔除 authorization | 补 `include={"authorization"}` |
| 每次请求多一跳 307 | URL 斜杠和挂载方式不匹配 | 挂载版连 `/mcp/`，独立跑连 `/mcp` |

## 和真实项目的关系

这份 demo 是**自包含**的：内存存数据、明文密码、密钥写在常量里，只够演示。

搬进生产要换三样：`service.py` 的 dict 换数据库、`config.py` 的常量换 `.env`、
`security.py` 的 `USERS` 换用户表。网关那层和挂载那四行基本不用动。
