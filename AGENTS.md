# AI Agent 开发指南

本文档为参与本 LangGraph FastAPI Agent 项目的 AI 编码助手提供必备约定。

## 常用命令

```bash
make install              # Install deps (uv sync) + pre-commit hooks
make dev                  # Dev server with hot reload (port 8000)
make lint                 # ruff check .
make format               # ruff format .
make typecheck            # uv run pyright (static type check)
make check                # lint + typecheck
make eval                 # Run LLM evals (interactive)
make eval-quick           # Run LLM evals (default settings)
make migrate              # Run DB migrations to latest (Alembic)
make docker-up            # Docker: API + DB (ENV=development by default)
make stack-up ENV=development  # Full stack: API + DB + Prometheus + Grafana
```

> 所有 server/DB/Docker 目标均接受 `ENV=development|staging|production|test`。
> 执行 `make help` 查看完整目标列表。

## 项目结构

```
app/
  api/v1/          # Route handlers (auth.py, chatbot.py, api.py)
  core/
    config.py      # Pydantic Settings config
    database.py    # Async DB setup
    langgraph/     # LangGraph agent graph + tools
    logging.py     # structlog setup
    llm.py         # LLM service with retry logic
    limiter.py     # Rate limiting (slowapi)
    metrics.py     # Prometheus metrics
    middleware.py  # ASGI middleware
    prompts/       # System prompts
  models/          # SQLModel ORM models
  schemas/         # Pydantic request/response schemas + graph state
  services/        # Business logic services
  utils/           # Shared utilities
evals/             # LLM evaluation framework (Langfuse-based)
scripts/           # Environment setup, Docker build scripts
```

## 项目概览

这是一个可投入生产的 AI Agent 应用，技术栈如下：
- **LangGraph**：用于有状态、多步骤的 AI Agent 工作流
- **FastAPI**：用于高性能异步 REST API 端点
- **Langfuse**：用于 LLM 可观测性与链路追踪
- **PostgreSQL + pgvector**：用于长期记忆存储（mem0ai）
- **JWT 认证**：配合会话管理
- **Prometheus + Grafana**：用于监控

## 速查：关键规则

### 导入规则
- **所有 import 必须置于文件顶部**——禁止在函数或类内部添加 import

### 日志规则
- 所有日志必须使用 **structlog**
- 日志事件名必须为 **小写加下划线**（lowercase_with_underscores）格式，例如 `"user_login_successful"`
- **禁止在 structlog 事件中使用 f-string**——变量必须作为 kwargs 传入
- 使用 `logger.exception()` 而非 `logger.error()`，以保留 traceback
- 示例：`logger.info("chat_request_received", session_id=session.id, message_count=len(messages))`

### 重试规则
- **必须使用 tenacity 库**实现重试逻辑
- 按指数退避进行配置
- 示例：`@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))`

### 输出规则
- **必须启用 rich 库**输出格式化控制台内容
- 使用 rich 渲染进度条、表格、面板与格式化文本

### 缓存规则
- **只缓存成功响应**，禁止缓存错误
- 依据数据易变程度设置合适的缓存 TTL

### FastAPI 规则
- 所有路由必须带限流装饰器
- 服务、数据库连接与认证必须使用依赖注入
- 所有数据库操作必须为异步

## 代码风格约定

### Python/FastAPI
- 异步操作使用 `async def`
- 所有函数签名必须带类型提示
- 优先使用 Pydantic 模型，而非原始字典
- 采用函数式、声明式编程；除服务与 Agent 外避免使用类
- 文件命名：小写加下划线（例如 `user_routes.py`）
- 使用 RORO 模式（Receive an Object, Return an Object）

### 错误处理
- 在函数开头处理错误
- 错误分支使用提前返回（early return）
- 将正常路径放在函数末尾
- 前置条件使用守卫子句（guard clause）
- 预期错误使用 `HTTPException`，并设置合适的状态码

## LangGraph 与 LangChain 模式

### 图结构
- 使用 `StateGraph` 构建 AI Agent 工作流
- 使用 Pydantic 模型定义清晰的状态 schema（参见 `app/schemas/graph.py`）
- 生产工作流使用 `CompiledStateGraph`
- 实现 `AsyncPostgresSaver` 以支撑检查点（checkpointer）与持久化
- 使用 `Command` 控制节点之间的图流转

### 链路追踪
- 使用 Langfuse 提供的 LangChain `CallbackHandler` 追踪所有 LLM 调用
- 所有 LLM 操作必须启用 Langfuse 追踪

### 记忆（mem0ai）
- 使用 `AsyncMemory` 进行语义记忆存储
- 按 user_id 分用户存储记忆，以实现个性化体验
- 使用异步方法：`add()`、`get()`、`search()`、`delete()`

## 认证与安全

- 使用 JWT token 做认证
- 实现基于会话的用户管理（参见 `app/api/v1/auth.py`）
- 受保护端点必须使用 `get_current_session` 依赖
- 敏感数据必须存放于环境变量
- 所有用户输入必须用 Pydantic 模型校验

## 数据库操作

- ORM 模型使用 SQLModel（融合 SQLAlchemy + Pydantic）
- 在 `app/models/` 目录下定义模型
- 使用 asyncpg 执行异步数据库操作
- 使用 LangGraph 的 AsyncPostgresSaver 为 Agent 做检查点（checkpointer）

## 性能规范

- 尽量减少阻塞式 I/O 操作
- 所有数据库与外部 API 调用必须异步
- 对高频访问数据实施缓存
- 数据库连接使用连接池
- 用流式响应优化 LLM 调用

## 可观测性

- 在所有 Agent 操作中接入 Langfuse 做 LLM 追踪
- 导出 Prometheus 指标以观测 API 性能
- 使用带上下文绑定（request_id、session_id、user_id）的结构化日志
- 跟踪 LLM 推理耗时、token 用量与成本

## 测试与评测

- 为 LLM 输出实现基于指标的评测（参见 `evals/` 目录）
- 在 `evals/metrics/prompts/` 中以 markdown 文件形式创建自定义评测指标
- 使用 Langfuse trace 作为评测数据源
- 生成含成功率的 JSON 报告

## 配置管理

- 使用区分环境的配置文件（`.env.development`、`.env.staging`、`.env.production`）
- 使用 Pydantic Settings 实现类型安全配置（参见 `app/core/config.py`）
- 禁止硬编码密钥或 API key

## 关键依赖

- **FastAPI**：Web 框架
- **LangGraph**：Agent 工作流编排
- **LangChain**：LLM 抽象与工具
- **Langfuse**：LLM 可观测性与链路追踪
- **Pydantic v2**：数据校验与配置
- **structlog**：结构化日志
- **mem0ai**：长期记忆管理
- **PostgreSQL + pgvector**：数据库与向量存储
- **SQLModel**：数据库模型 ORM
- **tenacity**：重试逻辑
- **rich**：终端格式化
- **slowapi**：限流
- **prometheus-client**：指标采集

## 本项目的 10 条戒律

1. 所有路由必须带限流装饰器
2. 所有 LLM 操作必须接入 Langfuse 追踪
3. 所有异步操作必须妥善处理错误
4. 所有日志必须遵循结构化日志规范，事件名使用小写下划线
5. 所有重试必须使用 tenacity 库
6. 所有控制台输出应使用 rich 格式化
7. 所有缓存只允许存放成功响应
8. 所有 import 必须置于文件顶部
9. 所有数据库操作必须为异步
10. 所有端点必须带完整类型提示与 Pydantic 模型
11. 所有代码必须通过 `make typecheck`（pyright standard 模式）

## 常见陷阱

- ❌ 在 structlog 事件中使用 f-string
- ❌ 在函数内部添加 import
- ❌ 路由遗漏限流装饰器
- ❌ LLM 调用缺失 Langfuse 追踪
- ❌ 缓存错误响应
- ❌ 异常处理使用 `logger.error()` 而非 `logger.exception()`
- ❌ 未使用异步而阻塞 I/O
- ❌ 硬编码密钥或 API key
- ❌ 函数签名缺失类型提示

## 修改代码时

修改代码之前：
1. 先阅读现有实现
2. 检查代码库中是否存在同类模式
3. 确保与既有代码风格一致
4. 添加结构化格式的日志
5. 包含带提前返回的错误处理
6. 添加类型提示与 Pydantic 模型
7. 确认 LLM 调用已启用 Langfuse 追踪

## 参考资料

- LangGraph 文档：https://langchain-ai.github.io/langgraph/
- LangChain 文档：https://python.langchain.com/docs/
- FastAPI 文档：https://fastapi.tiangolo.com/
- Langfuse 文档：https://langfuse.com/docs
