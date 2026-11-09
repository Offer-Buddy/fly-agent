# Fly Agent

基于 FastAPI + LangGraph 的 AI 智能体应用脚手架。有状态对话、长期记忆、工具调用、可观测性、限流与认证这些生产级难点已经接好，新增业务只需要往 `app/` 里长。

**给 AI 工程师用的地基**，不是教程项目。

---

## 包含内容

- **LangGraph** 有状态智能体，支持检查点（checkpointer）、工具调用与人工介入（HITL）
- **长期记忆**：基于 mem0 + pgvector——按用户进行语义检索，并由缓存支撑
- **LLM 服务**：支持循环模型降级、指数退避重试以及总超时预算
- **Langfuse** 追踪覆盖全部 LLM 调用（自建栈，`make langfuse-up` 一条命令拉起）
- **JWT 认证**与会话管理；基于 slowapi 的限流
- **Alembic** 迁移；可选的 Redis 缓存层
- **结构化日志**，每一行都带请求/会话/用户上下文

## 快速开始

```bash
git clone <repo-url> fly-agent && cd fly-agent
cp .env.example .env.development   # fill in your keys
make install
make docker-up                     # starts API + PostgreSQL + Redis
```

打开 [http://localhost:8000/docs](http://localhost:8000/docs) 查看交互式 API 文档。

> 若要在不使用 Docker 的情况下本地开发，请参阅 [docs/getting-started.md](docs/getting-started.md)。

## 文档

| 指南 | 内容简介 |
|---|---|
| [入门指南](docs/getting-started.md) | 前置条件、本地环境搭建、首次 API 调用 |
| [架构](docs/architecture.md) | 系统设计、请求流程、组件图 |
| [配置](docs/configuration.md) | 全部环境变量及其默认值 |
| [认证](docs/authentication.md) | JWT 流程、会话、端点参考 |
| [数据库与迁移](docs/database.md) | Schema、Alembic 迁移、pgvector |
| [LLM 服务](docs/llm-service.md) | 模型、重试、降级、超时预算 |
| [记忆](docs/memory.md) | mem0 长期记忆、缓存层 |
| [可观测性](docs/observability.md) | Langfuse 自建追踪、结构化日志、请求耗时、性能分析 |
| [评测](docs/evaluation.md) | 评测框架、自定义指标、报告 |
| [Docker](docs/docker.md) | Docker、Compose、完整监控栈 |

## 项目结构

```
app/
  api/v1/          # Route handlers
  core/
    langgraph/     # Agent graph + tools
    prompts/       # System prompt template
    cache.py       # Redis + in-memory fallback
    config.py      # Settings
    middleware.py  # Metrics, logging context, profiling
    limiter.py     # Rate limiting
  models/          # SQLModel ORM models
  schemas/         # Pydantic request/response schemas
  services/        # LLM, database, memory services
alembic/           # Database migrations
evals/             # LLM evaluation framework
```

## 贡献

欢迎提交 PR。请先阅读 [docs/getting-started.md](docs/getting-started.md) 完成环境搭建，然后遵循 [AGENTS.md](AGENTS.md) 中的编码规范。

安全问题请私下报告——参见 [SECURITY.md](SECURITY.md)。

## 许可证

参见 [LICENSE](LICENSE)。

## 常见问题

### 通用

**这个脚手架是什么？**
一个基于 FastAPI + LangGraph 构建、可用于生产环境的 AI 智能体后端基础框架。它把原本需要你手工串联的组件打包在一起：有状态对话、长期记忆、工具调用、可观测性、限流与 JWT 认证。

**它与基础的 LangGraph 搭建方式有何不同？**
LangGraph 的基础快速开始止步于「智能体能在本地跑起来」。本脚手架则补充了 Alembic 迁移、mem0 + pgvector 长期记忆、自建 Langfuse 追踪、JWT 会话、slowapi 限流、带每次请求上下文的结构化日志，以及具备循环降级能力的 LLM 服务——这些都是你原本需要单独构建的生产级关注点。

### 安装与配置

**我需要 Docker 吗？**
推荐但并非必需。`make docker-up` 会一并启动 API、PostgreSQL 与 Redis；`make langfuse-up` 另外拉起自建追踪栈（可选）。若仅做本地环境搭建，请参阅 [docs/getting-started.md](docs/getting-started.md)。

**支持哪些 LLM 供应商？**
任何 LangChain 的 [`init_chat_model`](https://python.langchain.com/docs/how_to/chat_models_universal_init/) 所支持的供应商均可。`app/services/llm/registry.py` 中的 `LLMRegistry` 通过 `init_chat_model` 构建每一个模型，因此像 `gpt-5.6-luna` 这样的裸名称仍会照旧解析为 OpenAI 模型，而 `provider:model` 形式的字符串（例如 `anthropic:claude-opus-4-6`、`ollama:llama3`）则可加入其他任意供应商。安装该供应商对应的 LangChain 集成包——参见 `pyproject.toml` 中的 `anthropic` / `ollama` / `google-genai` 附加依赖——并通过该供应商自己的环境变量设置其 API 密钥（例如 `ANTHROPIC_API_KEY`）。默认模型通过 `.env.development` 中的 `DEFAULT_LLM_MODEL` 配置。

```bash
# uv sync with the anthropic extra installed
uv sync --extra anthropic
```

```python
# app/services/llm/registry.py
LLM_CONFIGS: List[Dict[str, Any]] = [
    {"name": "gpt-5.6-luna", "model": "gpt-5.6-luna", "kwargs": {...}},  # OpenAI, unchanged
    {"name": "claude-opus", "model": "anthropic:claude-opus-4-6", "kwargs": {"temperature": 0.2}},
]
```

**如何配置长期记忆？**
长期记忆采用自托管方式：mem0 在进程内运行，并通过 pgvector 持久化到你已有的 PostgreSQL 中——不需要单独的 mem0 云账号或 API 密钥。你只需要一个可用的 `OPENAI_API_KEY`（用于事实抽取 + 向量嵌入）以及已启用的 pgvector 扩展。详情参见 [docs/memory.md](docs/memory.md)。

### 开发

**如何添加自定义工具？**
在 `app/core/langgraph/tools/` 下放一个用 LangChain `@tool` 装饰的函数，并在该包导出的 `tools` 列表中注册它。智能体下次启动时即可加载，无需改动图结构。

**可以运行多个智能体吗（supervisor、子智能体）？**
可以。脚手架默认只提供一个智能体，但任何已编译的图都可以作为主图的一个节点加入。它共享 `GraphState`（因此 `messages` 可以流通），并继承父级的 Postgres 检查点（checkpointer），所以无需额外的持久化配置。在 `app/core/langgraph/graph.py` 的 `create_graph()` 中：

```python
research = StateGraph(GraphState)
research.add_node("research", research_node)
research.set_entry_point("research")
research_agent = research.compile()  # no checkpointer: inherits the parent's

graph_builder.add_node("chat", self._chat, destinations=("tool_call", "research_agent", END))
graph_builder.add_node("research_agent", research_agent)
graph_builder.add_edge("research_agent", "chat")
```

随后 `chat` 便充当 supervisor：返回 `Command(goto="research_agent")` 即可委派任务，子智能体的回复会回到 `chat`。每新增一个专精智能体就增加一个节点。若想使用预构建版本，参见 [`langgraph-supervisor`](https://github.com/langchain-ai/langgraph-supervisor-py)。

**LLM 服务如何处理故障？**
分两层：(1) 通过 `tenacity` 对每次调用做指数退避重试；(2) **循环降级**——若当前模型用完重试次数，服务会轮转到 `LLMRegistry` 中的下一个模型继续执行。总超时预算会为整个调用设上限，使延迟保持在可控范围内。参见 [docs/llm-service.md](docs/llm-service.md)。

**可以不使用 Langfuse 吗？**
可以。设置 `LANGFUSE_TRACING_ENABLED=false`（或省略 Langfuse 相关密钥）。智能体照常运行；结构化日志仍会记录请求/会话/用户上下文。

### 故障排查

**API 无法启动**
- 确认 PostgreSQL 正在运行（`make docker-up` 会连同 API 一起启动它）
- 确认 `.env.development` 存在——从 `.env.example` 复制并填入必需的密钥
- 执行迁移：`make migrate`

**记忆 / 语义检索没有返回结果**
- 检查你的 PostgreSQL 实例是否已启用 `pgvector` 扩展
- 确认 `OPENAI_API_KEY` 有效（mem0 需调用 OpenAI 进行事实抽取 + 向量嵌入）
- 检查 `.env.development` 中是否已设置 `LONG_TERM_MEMORY_MODEL` 与 `LONG_TERM_MEMORY_EMBEDDER_MODEL`

**限流过于严格**
限流阈值定义在 `app/core/limiter.py`（slowapi）中。可调整各路由的装饰器或该文件中的默认速率。相关环境变量参见 [docs/configuration.md](docs/configuration.md)。
