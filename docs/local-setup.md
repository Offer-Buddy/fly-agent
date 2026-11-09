# 本地运行指南（Windows · 无 Docker）

这套环境不依赖 Docker，用**便携版 PostgreSQL 17 + pgvector** 直接跑在本机，
LLM 走 **DeepSeek**（OpenAI 兼容端点），长期记忆的向量化走 **DashScope**。

---

## 一、日常启动 / 停止

| 操作 | 方式 |
|---|---|
| 启动 | 双击项目根目录的 **`start-local.bat`** |
| 停止 | 双击项目根目录的 **`stop-local.bat`** |

`start-local.bat` 会依次：拉起 PostgreSQL（5432）→ 拉起 API（8000）→ 自动打开 Swagger 文档页。
已经启动过的组件会被跳过，所以重复双击是安全的。

启动成功后打开：

| 地址 | 用途 |
|---|---|
| http://127.0.0.1:8000/docs | **Swagger 交互式接口文档**（推荐，可直接点着测） |
| http://127.0.0.1:8000/health | 健康检查（含数据库连通性） |
| http://127.0.0.1:8000/redoc | ReDoc 版文档 |

---

## 二、环境信息（当前这台机器）

| 项目 | 值 |
|---|---|
| 项目目录 | `E:\workspace\fly-agent` |
| Python | 3.13（虚拟环境 `.venv`，依赖已装好） |
| PostgreSQL | 17.6，二进制在 `C:\Users\jiang\AppData\Local\fly-agent\pgsql` |
| 数据目录 | `C:\Users\jiang\AppData\Local\fly-agent\pgdata` |
| 数据库 | `fly_agent_dev`（账号 `postgres` / 密码 `postgres`） |
| pgvector | 0.8.7（已启用，向量维度 1024） |
| 配置文件 | `.env.development`（已在 `.gitignore` 里，不会进仓库） |

数据库的临时建库、迁移都已执行完毕，无需再跑 `alembic`。

---

## 三、接口怎么用

完整链路是 **注册 → 登录 → 建会话 → 对话**。会话 token 与用户 token 是两套：
`/auth/session` 返回的那个 token 才能调 `/chatbot/*`。

### 1. 注册

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"you@example.com","password":"Secret123!","username":"you"}'
```

### 2. 登录（注意：参数是 form，不是 JSON，字段名是 `email`）

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "email=you@example.com&password=Secret123!"
```

返回 `access_token`（用户级 token）。

### 3. 创建会话

```bash
curl -X POST http://127.0.0.1:8000/api/v1/auth/session \
  -H "Authorization: Bearer <用户 token>"
```

返回 `session_id` 和 `token.access_token`（**会话级 token**，下面都用它）。

### 4. 对话

```bash
# 非流式
curl -X POST http://127.0.0.1:8000/api/v1/chatbot/chat \
  -H "Authorization: Bearer <会话 token>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"你好"}]}'

# 流式（SSE）
curl -N -X POST http://127.0.0.1:8000/api/v1/chatbot/chat/stream \
  -H "Authorization: Bearer <会话 token>" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"用三个要点说说 RAG"}]}'
```

### 5. 其它

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/chatbot/messages` | 取当前会话历史 |
| DELETE | `/api/v1/chatbot/messages` | 清空当前会话历史 |
| GET | `/api/v1/auth/sessions` | 列出我的所有会话 |
| PATCH | `/api/v1/auth/session/{id}/name` | 会话改名 |
| DELETE | `/api/v1/auth/session/{id}` | 删除会话 |

> 本机 curl 如果走了系统代理会报 `upstream connect failed`，加 `--noproxy '*'` 即可。
> 用浏览器打开 `/docs` 点着测最省事。

---

## 四、换模型 / 换 Key

只改 `.env.development`，**不用动代码**：

```env
OPENAI_API_KEY=<你的 key>
OPENAI_BASE_URL=https://api.deepseek.com/v1   # 任意 OpenAI 兼容端点
DEFAULT_LLM_MODEL=deepseek-v4-flash           # 首选模型
LLM_FALLBACK_MODELS=deepseek-v4-pro           # 失败时轮流切的后备模型（逗号分隔）
LONG_TERM_MEMORY_MODEL=deepseek-v4-flash      # 记忆事实抽取用的模型
```

长期记忆的向量化是独立通道（DeepSeek 没有 embeddings 接口，所以用 DashScope）：

```env
LONG_TERM_MEMORY_EMBEDDER_MODEL=text-embedding-v3
LONG_TERM_MEMORY_EMBEDDER_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LONG_TERM_MEMORY_EMBEDDER_API_KEY=<DashScope key>
LONG_TERM_MEMORY_EMBEDDING_DIMS=1024
```

改完 `.env.development` 后重启 API 生效（`--reload` 只监听代码文件，不监听 env）。

想接 OpenAI 官方：`OPENAI_BASE_URL` 留空、`DEFAULT_LLM_MODEL=gpt-5.4` 之类即可。

---

## 五、跑起来之后能验证什么

以下都实测通过：

- `/health` 200，`components.database = healthy`
- 注册 / 登录 / 建会话
- 非流式对话（DeepSeek 约 2–3 秒返回）
- 流式对话（逐 token SSE）
- 会话历史读写、会话删除
- **会话自动命名**：首条消息后由模型生成短标题（如「昆明大模型应用课程开发」）
- **跨会话长期记忆**：先告一次「我在昆明，做 LLM 课程」，换个新会话问「你记得我在哪吗」，
  Agent 能答出昆明 + 课程 + Python/LangGraph（数据落在 `longterm_memory` 表，4 条事实）

### 直接查数据库

```bash
export PGPASSWORD=postgres
"C:/Users/jiang/AppData/Local/fly-agent/pgsql/bin/psql.exe" -h 127.0.0.1 -U postgres -d fly_agent_dev

# 建表情况
\dt
# 长期记忆内容
select payload->>'data' from longterm_memory;
# 会话标题
select id, name from session order by created_at desc limit 5;
```

---

## 六、为适配本机做过的代码改动

原模板是给「纯 OpenAI + Docker」准备的，直接跑会在几处卡住。改动都做了通用化处理，
不是硬编码成本机专用的 hack：

| 文件 | 改动 | 原因 |
|---|---|---|
| `app/core/config.py` | 新增 `OPENAI_BASE_URL`、`LLM_FALLBACK_MODELS`；长期记忆新增 embedder 的 base_url / api_key / dims | 让 OpenAI 兼容端点可配 |
| `app/services/llm/registry.py` | `LLM_CONFIGS` 由环境变量生成（原来是 4 个硬编码 `gpt-*`）；`_build_llm` 注入 base_url 并固定 `model_provider="openai"` | 否则 `deepseek-*` 会被推断成 `langchain_deepseek` 而报缺包 |
| `app/services/llm/service.py` | 结构化输出从 `json_schema` 改为 `json_mode` | DeepSeek 思考模式既拒绝 `json_schema` 也拒绝强制 `tool_choice` |
| `app/services/session_naming.py` | 硬编码的 `gpt-5.4-nano` 改为 `DEFAULT_LLM_MODEL`；去掉 OpenAI 专属的 `reasoning` 参数 | 否则会话命名每次都失败 |
| `app/core/prompts/session_title.md` | 补一句「只输出 JSON」 | `json_mode` 要求提示词里出现 json 约定 |
| `app/services/memory.py` | embedder 支持独立端点和维度 | DeepSeek 无 embeddings API |

新增文件：`start-local.bat`、`stop-local.bat`、`scripts/local/start.ps1`、`scripts/local/stop.ps1`。

`ruff check` 与 `pyright` 均通过（0 error）。

---

## 七、已知限制

1. **Windows 上必须用 `--reload` 启动**。uvicorn 非 reload 模式会先用 ProactorEventLoop 建事件循环，
   与 psycopg 的异步连接不兼容，表现为连接池全部超时。脚本里已经带上了这个参数。
2. **Redis 没装**，当前走进程内内存缓存。单机开发够用；要多实例部署再装 Redis，
   在 `.env.development` 里填 `REDIS_HOST` 即可切换。
3. **Langfuse 追踪关闭**（`LANGFUSE_TRACING_ENABLED=false`），需要链路追踪时填三个 key 打开。
4. 长期记忆的事实抽取默认输出英文（模板自带 prompt 是英文），
   要中文可在 `app/core/prompts/memory_capture.md` 里改。
5. 工具里有 `duckduckgo_search`，国内网络环境下大概率拿不到结果；`ask_human` 会中断图等人工输入。
