# Fly Agent

基于 FastAPI + LangGraph 的 AI 智能体应用脚手架。有状态对话、长期记忆、工具调用、可观测性、限流与认证这些生产级难点已经接好，新增业务只需要往 `app/` 里长。

**给 AI 工程师用的地基**，不是教程项目。

---

## 包含内容

- **LangGraph** 有状态智能体：检查点（checkpointer）、工具调用、人工介入（HITL）
- **长期记忆**：mem0 + pgvector，按用户做语义检索，前面套一层缓存
- **LLM 服务**：指数退避重试 + 循环模型降级 + 总超时预算
- **Langfuse** 追踪覆盖全部 LLM 调用（外部独立部署，本仓库只填地址与 key）
- **JWT 认证**与会话管理；基于 slowapi 的限流
- **Alembic** 迁移；可选的 Redis 缓存层（没配 Redis 时自动降级为内存缓存）
- **结构化日志**，每行都带 request_id / session_id / user_id

## 快速开始

```bash
git clone <repo-url> fly-agent && cd fly-agent
cp .env.example .env.development   # 填入密钥
make install                       # uv sync + 装 pre-commit 钩子
make docker-up                     # 启动 API + PostgreSQL + Redis
```

Windows 上不想用 Docker，仓库根目录有两支脚本，会拉起便携版 PostgreSQL（含 pgvector）再启动 API：

```bash
start-local.bat   # 一键启动；已在跑的服务会跳过
stop-local.bat    # 停止 API 与本地 PostgreSQL
```

服务起来后，根路径 `GET /health` 返回各组件状态——数据库不可达时返回 503，方便负载均衡摘掉实例。

## 接口一览

业务路由全部挂在 `/api/v1` 下：

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/auth/register` | 注册 |
| POST | `/auth/login` | 登录，签发 JWT |
| POST | `/auth/session` | 新建会话 |
| GET | `/auth/sessions` | 列出当前用户的会话 |
| PATCH | `/auth/session/{session_id}/name` | 重命名会话 |
| DELETE | `/auth/session/{session_id}` | 删除会话 |
| POST | `/chatbot/chat` | 对话，一次性返回 |
| POST | `/chatbot/chat/stream` | 对话，SSE 流式返回 |
| GET | `/chatbot/messages` | 取历史消息 |
| DELETE | `/chatbot/messages` | 清空历史消息 |
| GET | `/health` | 轻量探活，返回状态与版本 |

另有根路径 `GET /`（返回服务名、版本与运行环境）和 `GET /health`（会额外探测数据库，不可达时返回 503）。注意根路径的 `/health` 与上表的 `/api/v1/health` 是两个端点：后者只做轻量探活，前者带依赖探测。

## 项目结构

```
fly-agent/
├── app/                          # 应用代码
│   ├── main.py                   # FastAPI 入口：中间件、生命周期、根路由、健康检查
│   ├── api/v1/                   # 路由层
│   │   ├── api.py                #   路由汇总
│   │   ├── auth.py               #   注册 / 登录 / 会话管理
│   │   └── chatbot.py            #   对话（普通 + 流式）、消息历史
│   ├── core/                     # 横切能力
│   │   ├── config.py             #   Pydantic Settings，全部配置项
│   │   ├── cache.py              #   Redis + 内存兜底
│   │   ├── limiter.py            #   slowapi 限流
│   │   ├── logging.py            #   structlog 配置
│   │   ├── middleware.py         #   日志上下文、请求耗时、按需性能剖析
│   │   ├── observability.py      #   Langfuse 初始化
│   │   ├── langgraph/
│   │   │   ├── graph.py          #   StateGraph 构建、节点与流转
│   │   │   └── tools/            #   ask_human（HITL）、duckduckgo_search
│   │   └── prompts/              #   提示词：system / 会话命名 / 记忆抽取
│   ├── models/                   # SQLModel 表：user / session / thread
│   ├── schemas/                  # Pydantic 请求响应模型 + GraphState
│   ├── services/                 # 业务服务
│   │   ├── database.py           #   用户 / 会话 CRUD
│   │   ├── memory.py             #   长期记忆（mem0 + pgvector）
│   │   ├── session_naming.py     #   会话自动命名
│   │   └── llm/                  #   模型注册表 + 调用服务（重试 / 降级）
│   └── utils/                    # JWT、图工具函数、输入清洗、后台任务
├── alembic/                      # 数据库迁移
│   ├── env.py                    #   迁移运行环境
│   └── versions/                 #   迁移脚本
├── evals/                        # LLM 评测：拉 trace → 打分 → 出报告
│   └── metrics/prompts/          #   指标提示词（简洁/幻觉/有用/相关/有毒）
├── scripts/                      # 工具脚本
│   ├── local/                    #   便携版 PostgreSQL + API 一键起停
│   ├── langfuse_smoke.py         #   链路连通性自检
│   ├── trace_report.py           #   追踪数据的终端报表
│   ├── gen_settings_docs.py      #   配置三处一致性自查
│   └── lint_course_html.py       #   课程 HTML 交付前机检
├── poc/                          # 框架选型验证（baseline / LangGraph / CrewAI / AutoGen）
├── docker/initdb/                # 建库时启用 pgvector
├── docker-compose.yml            # API + PostgreSQL + Redis
├── Dockerfile
├── pyproject.toml                # 依赖清单与 ruff / pytest 配置
├── uv.lock                       # 锁定版本，CI 与镜像构建走 --frozen
├── Makefile                      # 开发命令入口，make help 看全部目标
├── AGENTS.md                     # 编码规范，改代码前先读
└── SECURITY.md
```

`start-local.bat` / `stop-local.bat` 只是 `scripts/local/` 下两个 PowerShell 脚本的包装（双击运行用）。

`docs/` 目录放工程文档与课程稿，**只在本地维护，不入版本库**。

## 贡献

欢迎提交 PR。环境搭建见上面的「快速开始」，编码规范见 [AGENTS.md](AGENTS.md)——日志、重试、限流、缓存、异步这几块的约定都写在那里面。

安全问题请私下报告，参见 [SECURITY.md](SECURITY.md)。

## 许可证

参见 [LICENSE](LICENSE)。

## 常见问题

### 通用

**这个脚手架是什么？**
一个基于 FastAPI + LangGraph 构建、可用于生产环境的 AI 智能体后端基础框架。它把原本需要手工串联的组件打包在一起：有状态对话、长期记忆、工具调用、可观测性、限流与 JWT 认证。

**它与基础的 LangGraph 搭建方式有何不同？**
LangGraph 的基础快速开始止步于「智能体能在本地跑起来」。本脚手架补上了 Alembic 迁移、mem0 + pgvector 长期记忆、Langfuse 追踪、JWT 会话、slowapi 限流、带每次请求上下文的结构化日志，以及具备循环降级能力的 LLM 服务——这些都是原本需要单独构建的生产级关注点。

### 安装与配置

**我需要 Docker 吗？**
推荐但非必需。`make docker-up` 会一并启动 API、PostgreSQL 与 Redis。Windows 上也可以直接跑 `start-local.bat`，用便携版 PostgreSQL 免 Docker 本地开发。Langfuse 不在这份编排里——它由你单独部署，本项目填好 `LANGFUSE_HOST` 与 key 即可。

**支持哪些 LLM 供应商？**
任何 LangChain 的 [`init_chat_model`](https://python.langchain.com/docs/how_to/chat_models_universal_init/) 所支持的供应商均可。模型清单不写死在代码里，而是由 `.env.development` 的 `DEFAULT_LLM_MODEL` 加 `LLM_FALLBACK_MODELS` 生成：

```bash
DEFAULT_LLM_MODEL=gpt-5.6-luna                        # 对话用，也是降级的起点
LLM_FALLBACK_MODELS=deepseek-v4-pro,anthropic:claude-opus-4-6
```

裸模型名（如 `gpt-5.6-luna`）按 OpenAI 协议调用；配了 `OPENAI_BASE_URL` 时，任何裸名称都会走那个 OpenAI 兼容端点；`provider:model` 形式（如 `anthropic:claude-opus-4-6`、`ollama:llama3`）则路由到对应供应商。换供应商只需装上它的集成包、改环境变量，代码不用动：

```bash
uv sync --extra anthropic    # 另有 ollama / google-genai 两个 extra
```

**如何配置长期记忆？**
长期记忆采用自托管方式：mem0 在进程内运行，并通过 pgvector 持久化到你已有的 PostgreSQL 中——不需要单独的 mem0 云账号或 API 密钥。你只需要一个可用的 `OPENAI_API_KEY`（用于事实抽取 + 向量嵌入）以及已启用的 pgvector 扩展。抽取与嵌入各用哪个模型，由 `LONG_TERM_MEMORY_MODEL` 与 `LONG_TERM_MEMORY_EMBEDDER_MODEL` 决定。

### 开发

**如何添加自定义工具？**
在 `app/core/langgraph/tools/` 下放一个用 LangChain `@tool` 装饰的函数，并在该包 `__init__.py` 导出的 `tools` 列表中注册它。智能体下次启动时即可加载，无需改动图结构。

**可以运行多个智能体吗（supervisor、子智能体）？**
可以。脚手架默认只提供一个智能体（`chat` ↔ `tool_call` 两节点），但任何已编译的图都可以作为主图的一个节点加入。它共享 `GraphState`（因此 `messages` 可以流通），并继承父级的 Postgres 检查点，所以无需额外的持久化配置。在 `app/core/langgraph/graph.py` 的 `create_graph()` 中：

```python
research = StateGraph(GraphState)
research.add_node("research", research_node)
research.set_entry_point("research")
research_agent = research.compile()  # 不带 checkpointer，继承父图的

graph_builder.add_node("chat", self._chat, destinations=("tool_call", "research_agent", END))
graph_builder.add_node("research_agent", research_agent)
graph_builder.add_edge("research_agent", "chat")
```

随后 `chat` 便充当 supervisor：返回 `Command(goto="research_agent")` 即可委派任务，子智能体的回复会回到 `chat`。每新增一个专精智能体就增加一个节点。若想用预构建版本，参见 [`langgraph-supervisor`](https://github.com/langchain-ai/langgraph-supervisor-py)。

**LLM 服务如何处理故障？**
分两层：(1) 通过 `tenacity` 对每次调用做指数退避重试；(2) **循环降级**——当前模型用完重试次数后，服务会轮转到 `LLMRegistry` 里的下一个模型继续执行，顺序就是 `DEFAULT_LLM_MODEL` 打头、`LLM_FALLBACK_MODELS` 依次跟上。总超时预算给整个调用设上限，使延迟保持在可控范围内。

**可以不使用 Langfuse 吗？**
可以。设置 `LANGFUSE_TRACING_ENABLED=false`（或省略 Langfuse 相关密钥）。智能体照常运行；结构化日志仍会记录请求/会话/用户上下文。

### 故障排查

**API 无法启动**
- 确认 PostgreSQL 正在运行（`make docker-up` 会连同 API 一起启动它，或用 `start-local.bat`）
- 确认 `.env.development` 存在——从 `.env.example` 复制并填入必需的密钥
- 执行迁移：`make migrate`

**记忆 / 语义检索没有返回结果**
- 检查 PostgreSQL 实例是否已启用 `pgvector` 扩展
- 确认 `OPENAI_API_KEY` 有效（mem0 需调用 OpenAI 做事实抽取 + 向量嵌入）
- 检查 `.env.development` 中是否已设置 `LONG_TERM_MEMORY_MODEL` 与 `LONG_TERM_MEMORY_EMBEDDER_MODEL`

**限流过于严格**
默认值在 `app/core/config.py` 的 `_RATE_LIMIT_ENDPOINT_DEFAULTS`，可用 `.env.development` 里的 `RATE_LIMIT_<接口名>` 覆盖，例如：

```bash
RATE_LIMIT_CHAT="100 per minute"
RATE_LIMIT_DEFAULT="1000 per day,200 per hour"
```

按端点收放的粒度见 `.env.example`。装饰器本身在 `app/core/limiter.py` 与各路由上。
