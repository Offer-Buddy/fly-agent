# 快速开始

## 前置条件

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) — `pip install uv`
- Docker + Docker Compose（本地开发推荐）
- OpenAI API 密钥
- Langfuse 实例（可选 —— 自建或云端；没有就设 `LANGFUSE_TRACING_ENABLED=false` 跳过）

## 方式 A：Docker（推荐）

最快的启动方式。一条命令即可拉起 API 和带 pgvector 的 PostgreSQL。

```bash
git clone <repo-url> my-agent
cd my-agent

# Copy and fill in your env file
cp .env.example .env.development
# Required: OPENAI_API_KEY, JWT_SECRET_KEY
# Optional: LANGFUSE_* keys (or set LANGFUSE_TRACING_ENABLED=false)

make install       # installs Python deps + pre-commit hooks
make docker-up     # starts API (port 8000) + PostgreSQL
make docker-migrate # runs Alembic migrations inside the app container
```

打开 [http://localhost:8000/docs](http://localhost:8000/docs)。

## 方式 B：本地 Python

```bash
git clone <repo-url> my-agent
cd my-agent

cp .env.example .env.development
# Fill in: OPENAI_API_KEY, JWT_SECRET_KEY, POSTGRES_* (point to your DB)

make install       # installs deps + pre-commit hooks
make migrate       # creates tables via Alembic
make dev           # starts server with hot reload on port 8000
```

## 第一次调用接口

### 1. 注册用户

```bash
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com", "password": "Secret123!", "username": "you"}'  # pragma: allowlist secret
```

返回 `user_id` 和一个 JWT token。

### 2. 创建会话

```bash
curl -X POST http://localhost:8000/api/v1/auth/session \
  -H "Authorization: Bearer <token from step 1>"
```

返回 `session_id` 和一个绑定该会话的 JWT。

### 3. 对话

```bash
curl -X POST http://localhost:8000/api/v1/chatbot/chat \
  -H "Authorization: Bearer <session token>" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello!"}]}'
```

或者用流式接口获取实时响应：

```bash
curl -X POST http://localhost:8000/api/v1/chatbot/chat/stream \
  -H "Authorization: Bearer <session token>" \
  -H "Content-Type: application/json" \
  -d '{"messages": [{"role": "user", "content": "Hello!"}]}'
```

## 定制 Agent

你大概率会改动的部分：

| 内容 | 位置 |
|---|---|
| Agent 人格与指令 | `app/core/prompts/system.md` |
| 可用工具 | `app/core/langgraph/tools.py` |
| LLM 模型与回退顺序 | `app/services/llm.py` → `LLMRegistry.LLMS` |
| 记忆集合名 | `.env` 里的 `LONG_TERM_MEMORY_COLLECTION_NAME` |

## 运行 pre-commit 钩子

钩子在 `git commit` 时自动运行。手动执行：

```bash
make pre-commit
```

钩子包括：行尾空白检查、YAML/TOML/JSON 校验、密钥检测、ruff lint + format。

## 故障排查

**启动时数据库连接报错**
确认 PostgreSQL 已运行，且 `.env` 里的 `POSTGRES_*` 变量与之匹配。使用 Docker 时：`make docker-up` 会处理这些（包括数据库迁移）。

**`could not translate host name "db"`**
`POSTGRES_HOST=db` 只在 Docker 网络*内部*才能解析（它是 Compose 的服务名）。如果你在宿主机上执行命令（例如本地 Python 流程里的 `make migrate` 或 `make dev`），请改成 `POSTGRES_HOST=localhost` —— 数据库端口已通过 `docker-compose.yml` 发布到宿主机。在容器内部则保持 `db` 不变。

**`detect-secrets` 阻止提交**
如果是误报，在被标记那一行的末尾加上 `# pragma: allowlist secret`。

**Langfuse 报错**
在 `.env` 里设 `LANGFUSE_TRACING_ENABLED=false`，开发期间彻底关闭链路追踪。
