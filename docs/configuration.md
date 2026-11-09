# 配置系统使用指南

本项目所有配置都集中在 `app/core/config.py`，基于 **pydantic-settings** 实现：字段即配置项，导入即校验，类型错了启动就失败。

---

## 1. 一分钟上手

```bash
# 1. 从模板创建环境文件
cp .env.example .env.development

# 2. 至少填这三项
#    OPENAI_API_KEY=你的 key
#    JWT_SECRET_KEY=至少 32 位的随机串
#    POSTGRES_* = 你的数据库连接

# 3. 启动
make dev
```

启动时会打印两行，确认加载了哪个环境、哪个文件：

```
Loading environment: development
Loaded environment from E:\workspace\fly-agent\.env.development
```

想看当前生效的完整配置：

```bash
uv run python -c "from app.core.config import settings; print(settings.model_dump_json(indent=2))"
```

---

## 2. 配置是怎么解析的

优先级从高到低，四层：

| 层级 | 来源 | 例子 | 说明 |
| --- | --- | --- | --- |
| 1 | 初始化参数 | `Settings(MAX_TOKENS=3000)` | 测试、脚本里局部覆盖用 |
| 2 | OS 环境变量 | `MAX_TOKENS=3000 make dev` | 容器、CI、临时调试最常用 |
| 3 | env 文件 | `.env.development` 里一行 | 日常开发主入口 |
| 4 | 环境相关默认值 | `_ENV_SPECIFIC_DEFAULTS` | 仅在第 1–3 层都没给值时生效 |
| 5 | 字段默认值 | `MAX_TOKENS: int = 2000` | 兜底 |

同名字段永远是**高层覆盖低层**，不会合并。实测：

```bash
# .env.development 里写的是 PROJECT_NAME="Fly Agent"
PROJECT_NAME="Override" uv run python -c "from app.core.config import settings; print(settings.PROJECT_NAME)"
# 输出：Override
```

### 字段名就是环境变量名

- 全大写，**大小写敏感**：写 `project_name`（小写）不会生效，写 `PROJECT_NAME` 才行。
  > Windows 的 OS 环境变量名本身大小写不敏感，系统会统一成大写；`case_sensitive=True` 主要约束的是 **env 文件里的键名**。
- 唯一的例外是 `ENVIRONMENT` 字段：它的环境变量名沿用历史叫法 `APP_ENV`（两个名字都认）。

---

## 3. env 文件是怎么挑的

`APP_ENV` 决定用哪个文件，候选顺序**从上往下取第一个存在的**（不合并多个文件）：

| 顺序 | 文件名 |
| --- | --- |
| 1 | `.env.<APP_ENV>.local` |
| 2 | `.env.<APP_ENV>` |
| 3 | `.env.local` |
| 4 | `.env` |

三个要点：

1. **只认 OS 环境变量里的 `APP_ENV`**。文件里写 `APP_ENV=production` 不能决定"加载哪个文件"——那时文件已经选完了。
2. **绝对路径定位**，基于项目根目录推算，与启动时的工作目录无关。
3. 一个都没有时也能启动，全部走默认值（生产环境会因 `JWT_SECRET_KEY` 校验失败，见第 10 节）。

```bash
# 本地切环境
APP_ENV=staging make dev
# Docker 里 compose 已经帮你传了：APP_ENV: ${APP_ENV:-development}
```

---

## 4. 值怎么写：类型规则

类型由字段声明决定，`str` 会自动转换，转换不了就报错。

| 类型 | 写法 | 说明 |
| --- | --- | --- |
| `str` | `LOG_LEVEL=INFO` | 引号可有可无 |
| `bool` | `DEBUG=true` | 认 `true/false`、`1/0`、`yes/no`、`on/off`、`t/f`，不区分大小写 |
| `int` | `MAX_TOKENS=2000` | 写成 `2000.5` 或 `lots` 会失败 |
| `float` | `DEFAULT_LLM_TEMPERATURE=0.2` | 同上 |
| `Path` | `LOG_DIR=logs` | 相对路径按运行时工作目录解析 |
| `list[str]` | `ALLOWED_ORIGINS=http://a.com,http://b.com` | **逗号分隔**，不是 JSON；两侧引号会被去掉 |
| `Enum` | `APP_ENV=production` | 也认 `prod` / `stage` 简写 |

### 列表的三种写法

```bash
ALLOWED_ORIGINS="http://localhost:3000,http://localhost:8000"   # 带引号
ALLOWED_ORIGINS=http://localhost:3000,http://localhost:8000     # 不带引号
LLM_FALLBACK_MODELS=gpt-4o                                       # 单值，无需逗号
```

> 注意：pydantic 默认把列表当 **JSON** 解析，本项目为此定义了一个专门的注解类型 `CSVList`
> （= `Annotated[list[str], NoDecode, BeforeValidator(_parse_csv)]`）。
> **新增列表型配置时，把字段类型标注成 `CSVList` 即可**，逗号解析、去引号、去空白全部自动生效；
> 不需要再去某个 `@field_validator` 里登记字段名（旧写法漏登记就会在运行时抛 JSON 解析错误）。

### 空值语义（容易踩）

| 写法 | 结果 |
| --- | --- |
| `REDIS_HOST=` | `""` → 视为"未启用 Redis"，自动退回进程内缓存 |
| `ALLOWED_ORIGINS=` | `["*"]` → 放行全部来源（保留旧行为） |
| `LLM_FALLBACK_MODELS=` | `[]` → 没有备用模型 |
| `EVALUATION_API_KEY=` | `""` → 回落到 `OPENAI_API_KEY` |
| 整个键不写 | 用字段默认值 |

---

## 5. 按环境自动切换的默认值

下表的值**只在对应键没被显式设置时**才生效（写在 env 文件或 OS 环境变量里就由你说了算）。

| 环境 | `DEBUG` | `LOG_LEVEL` | `LOG_FORMAT` | `RATE_LIMIT_DEFAULT` |
| --- | --- | --- | --- | --- |
| `development` | `true` | `DEBUG` | `console` | `1000 per day, 200 per hour` |
| `staging` | `false` | `INFO` | `json` | `500 per day, 100 per hour` |
| `production` | `false` | `WARNING` | `json` | `200 per day, 50 per hour` |
| `test` | `true` | `DEBUG` | `console` | `1000 per day, 1000 per hour` |

对应代码是 `app/core/config.py` 里的 `_ENV_SPECIFIC_DEFAULTS`，要调就改那里，别在业务代码里写 `if settings.ENVIRONMENT == ...`。

---

## 6. 代码里怎么用

**唯一入口**：模块级单例。

```python
from app.core.config import settings

max_tokens = settings.MAX_TOKENS          # int，有类型提示
db_host = settings.POSTGRES_HOST          # str
```

FastAPI 里想走依赖注入：

```python
from fastapi import Depends

from app.core.config import Settings, get_settings


@router.get("/info")
async def info(settings: Settings = Depends(get_settings)) -> dict:
    return {"model": settings.DEFAULT_LLM_MODEL}
```

`get_settings()` 带 `lru_cache`，多次调用不重复解析。

局部覆盖（测试、一次性脚本）：

```python
from app.core.config import Settings

s = Settings(MAX_TOKENS=4096, DEBUG=True)          # 只影响这个实例
s = Settings(_env_file=".env.test")                # 换一个 env 文件
s = Settings(_env_file=None)                       # 不读项目 env 文件
```

**不要**再在业务代码里写 `os.getenv("XXX")` —— 那样既没有类型校验，也不在这个教程的覆盖范围内。

---

## 7. 完整配置项参考

### 应用与 CORS

| 变量 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `APP_ENV` | enum | `development` | 环境：`development` / `staging` / `production` / `test`；**写错直接报错**，不再静默降级 |
| `PROJECT_NAME` | str | `FastAPI LangGraph Template` | 出现在 API 文档与日志 |
| `DEBUG` | bool | `false` | 开调试日志与性能剖析中间件 |
| `ALLOWED_ORIGINS` | list | `*` | CORS 白名单，逗号分隔；留空等价 `*` |

> `VERSION`、`DESCRIPTION`、`API_V1_STR`、`JWT_ALGORITHM` **不是环境变量**（`ClassVar`）：
> 前两个直接读 `pyproject.toml`（版本号只有一个来源），后两个是代码契约——改了要同步改
> 代码才能跑通，放进 `.env` 会被静默忽略。同理的还有 `CHECKPOINT_TABLES`。


### LLM

| 变量 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `OPENAI_API_KEY` | str | — | **必填**，主对话模型 key |
| `OPENAI_BASE_URL` | str | 空 | 走 OpenAI 兼容端点时填（DeepSeek / DashScope / vLLM / Atlas Cloud） |
| `DEFAULT_LLM_MODEL` | str | `gpt-5.6-luna` | 起始模型 |
| `LLM_FALLBACK_MODELS` | list | 空 | 备用模型链，逗号分隔，循环重试 |
| `DEFAULT_LLM_TEMPERATURE` | float | `0.2` | 采样温度 |
| `MAX_TOKENS` | int | `2000` | 单次回复上限 |
| `MAX_LLM_CALL_RETRIES` | int | `3` | 每个模型的重试次数 |
| `LLM_TOTAL_TIMEOUT` | int | `60` | 整条 fallback 链的总超时（秒） |
| `SESSION_NAMING_ENABLED` | bool | `true` | 用首条消息自动生成会话标题 |

### 长期记忆（mem0 + pgvector）

| 变量 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `LONG_TERM_MEMORY_MODEL` | str | `gpt-5-nano` | 抽记忆用的对话模型 |
| `LONG_TERM_MEMORY_EMBEDDER_MODEL` | str | `text-embedding-3-small` | 向量化模型 |
| `LONG_TERM_MEMORY_EMBEDDER_BASE_URL` | str | 空 | 向量模型可走独立端点（如 DeepSeek 无 embeddings，用 DashScope） |
| `LONG_TERM_MEMORY_EMBEDDER_API_KEY` | str | 空 | 为空则用 `OPENAI_API_KEY` |
| `LONG_TERM_MEMORY_EMBEDDING_DIMS` | int | `1536` | **必须与向量模型实际维度一致**，否则写入 pgvector 报错 |
| `LONG_TERM_MEMORY_COLLECTION_NAME` | str | `longterm_memory` | pgvector 集合名 |

### 鉴权

| 变量 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `JWT_SECRET_KEY` | str | — | **必填**，≥32 字符，非 `test` 环境会强校验 |
| `JWT_ACCESS_TOKEN_EXPIRE_DAYS` | int | `30` | token 有效期（天） |

### 数据库

| 变量 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `POSTGRES_HOST` | str | `localhost` | |
| `POSTGRES_PORT` | int | `5432` | |
| `POSTGRES_DB` | str | `food_order_db` | |
| `POSTGRES_USER` | str | `postgres` | |
| `POSTGRES_PASSWORD` | str | `postgres` | |
| `POSTGRES_POOL_SIZE` | int | `20` | 连接池大小 |
| `POSTGRES_MAX_OVERFLOW` | int | `10` | 池满后允许的额外连接 |

> `CHECKPOINT_TABLES`（LangGraph 检查点三张表）不是配置项，是 `ClassVar` 常量，改不了也不需要改。

### 缓存（Redis，可选）

不设 `REDIS_HOST` 时自动用进程内 TTL 缓存（多实例不共享）。

| 变量 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `REDIS_HOST` | str | 空 | 留空 = 关闭 Redis |
| `REDIS_PORT` | int | `6379` | |
| `REDIS_DB` | int | `1` | 默认 1，避免与共用实例（如 MaxKB 的 db 0）撞键 |
| `REDIS_PASSWORD` | str | 空 | |
| `REDIS_MAX_CONNECTIONS` | int | `20` | |
| `CACHE_TTL_SECONDS` | int | `60` | 记忆检索结果缓存 TTL |

### 限流

| 变量 | 类型 | 默认值 | 覆盖的接口 |
| --- | --- | --- | --- |
| `RATE_LIMIT_DEFAULT` | list | `200 per day,50 per hour` | 兜底 |
| `RATE_LIMIT_CHAT` | list | `30 per minute` | `POST /chat` |
| `RATE_LIMIT_CHAT_STREAM` | list | `20 per minute` | `POST /chat/stream` |
| `RATE_LIMIT_MESSAGES` | list | `50 per minute` | `GET/DELETE /messages` |
| `RATE_LIMIT_LOGIN` | list | `20 per minute` | `POST /auth/login` |
| `RATE_LIMIT_REGISTER` | list | `10 per hour` | `POST /auth/register` |
| `RATE_LIMIT_ROOT` | list | `10 per minute` | `GET /` |
| `RATE_LIMIT_HEALTH` | list | `20 per minute` | `GET /health` |

写法是 slowapi 语法：`次数 per 秒|分钟|小时|天`，可叠加多个用逗号分隔。配了 Redis 则多实例共享额度，否则每进程独立计数。

### 可观测性与评测

| 变量 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `LANGFUSE_TRACING_ENABLED` | bool | `true` | 关掉可完全停止链路追踪 |
| `LANGFUSE_PUBLIC_KEY` | str | 空 | |
| `LANGFUSE_SECRET_KEY` | str | 空 | |
| `LANGFUSE_HOST` | str | `http://localhost:3010` | 你自建实例的地址；走云端则设为 `https://cloud.langfuse.com` |
| `EVALUATION_LLM` | str | `gpt-5` | `make eval` 打分用模型 |
| `EVALUATION_BASE_URL` | str | `https://api.openai.com/v1` | |
| `EVALUATION_API_KEY` | str | 空 | 为空则用 `OPENAI_API_KEY` |
| `EVALUATION_SLEEP_TIME` | int | `10` | 评测请求间隔（秒） |

### 日志与性能剖析

| 变量 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `LOG_DIR` | path | `logs` | 启动时自动 `mkdir` |
| `LOG_LEVEL` | enum | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL`，写错启动即报错；见第 5 节的环境默认值 |
| `LOG_FORMAT` | enum | `json` | 只能 `json` 或 `console`；`console` 给本地看，`json` 给采集端 |
| `PROFILING_DIR` | path | `/tmp/fastapi_profiles` | 只有 `DEBUG=true` 时才写 |
| `PROFILING_THRESHOLD_SECONDS` | float | `2.0` | 超过该耗时才落盘，设 `0` 表示每个请求都剖析 |

---

## 8. 限流这类"动态键"配置

`RATE_LIMIT_<ENDPOINT>` 的键名是拼出来的，不是独立字段。新增一个受控接口：

1. 在 `_RATE_LIMIT_ENDPOINT_DEFAULTS` 里加一行，例如 `"upload": ["5 per minute"]`；
2. 之后 `RATE_LIMIT_UPLOAD` 就能覆盖它，`settings.RATE_LIMIT_ENDPOINTS["upload"]` 自动可用。

**注意**：这类键只从环境变量 / env 文件读，`Settings(RATE_LIMIT_CHAT="1 per minute")` 不会生效（它不在字段表里）。

---

## 9. 新增一个配置项（4 步）

```python
# 1) app/core/config.py —— 声明字段，类型 + 默认值 + 注释
SEARCH_TOP_K: int = 5
```

```python
# 2) 如果不同环境要给不同默认值，加进 _ENV_SPECIFIC_DEFAULTS
Environment.PRODUCTION: {"SEARCH_TOP_K": 10},
```

```bash
# 3) .env.example 补一行，给别人抄
SEARCH_TOP_K=5
```

```markdown
4) 本文档第 7 节对应表格补一行
```

不需要改 `__init__`，不需要手写 `int(...)` 转换，也不用加 `validate_xxx` 方法——类型校验和默认值由字段声明负责。
列表型字段记得用 `CSVList` 标注（见第 4 节）。

改完跑一次漂移自查，它会对比「代码里的字段」与「.env.example / 本文档」三处是否一致：

```bash
uv run python scripts/gen_settings_docs.py          # 只检查，列出差异并以非 0 退出
uv run python scripts/gen_settings_docs.py --emit   # 打印一份可粘贴的 Markdown 表格骨架
```

---

## 10. 校验与常见报错

配置在**导入时**一次校验完毕，所以问题会在启动瞬间暴露，而不是等到某个请求才炸。

### 类型写错

```
pydantic_core._pydantic_core.ValidationError: 1 validation error for Settings
MAX_TOKENS
  Input should be a valid integer, unable to parse string as an integer
  [type=int_parsing, input_value='lots', input_type=str]
```

按提示改 `.env` 即可。

### JWT 密钥不合规（生产必看）

```
RuntimeError: JWT_SECRET_KEY must be at least 32 characters long
```

规则：非 `test` 环境下，密钥长度 ≥32 且不能是 `changeme` 这类占位符。
（这里故意抛 `RuntimeError` 而不是 `ValidationError`：后者会把整份配置字典作为 `input_value` 回显，可能把 API Key 打进日志。）

### `APP_ENV` 拼错会静默降级

`APP_ENV=prodution`（少个 c）不会报错，会落到 `development`。改完环境记得看启动那行 `Loading environment: ...` 确认。

---

## 11. 测试里隔离配置

```python
import pytest

import app.core.config as config_module


@pytest.fixture
def settings_override(monkeypatch):
    monkeypatch.setenv("MAX_TOKENS", "777")
    monkeypatch.setenv("APP_ENV", "test")
    config_module.get_settings.cache_clear()   # 关键：清掉单例缓存
    yield config_module.Settings()
    config_module.get_settings.cache_clear()
```

只测某个实例、不碰全局单例时更简单：

```python
s = Settings(MAX_TOKENS=777)          # 初始化参数优先级最高
s = Settings(_env_file=".env.test")   # 换配置文件
s = Settings(_env_file=None)          # 不读项目 env 文件
```

⚠️ **一个坑**：模块导入时会执行 `load_dotenv()`，把 env 文件内容写进 `os.environ`（mem0 的 LLM 配置依赖它）。因此 `_env_file` 覆盖只对"OS 环境里没有的键"有效——已存在的键仍被 OS 环境变量压过。要彻底隔离，用 `monkeypatch.delenv("KEY")` 显式清掉，或在子进程里跑。

---

## 12. 部署注意

1. **环境变量优先于文件**。容器里用 `docker-compose.yml` 的 `environment:` 或 `-e` 传值，会覆盖镜像里的 `.env.*`。
2. **别提交 env 文件**。`.gitignore` 已排除 `.env`、`.env.development`、`.env.staging`、`.env.production`；`.env.example` 才是给团队抄的模板，里面只能有占位值。
3. **生产必设**：`APP_ENV=production`、强 `JWT_SECRET_KEY`、`LANGFUSE_TRACING_ENABLED`（按需）、数据库密码。
4. **启动即校验**：配置错了容器会立刻退出，`docker compose logs` 第一条就是原因，比运行几分钟后 500 好排查得多。
5. **敏感值**走 secret manager / K8s Secret，不要写进 compose 明文。

---

## 13. 从旧实现迁移过来（对照）

配置层已从「手写 `Settings` 类 + `os.getenv`」迁到 pydantic-settings，对外 API 不变：仍是 `from app.core.config import settings`。

| 旧实现 | 新实现 |
| --- | --- |
| `os.getenv("MAX_TOKENS", "2000")` + `int(...)` | `MAX_TOKENS: int = 2000` |
| `parse_list_from_env()` / `parse_dict_of_lists_from_env()` | `CSVList` 注解（`NoDecode` + `BeforeValidator`），不再需要维护字段名清单 |
| `__init__` 里 40+ 行赋值 | 字段声明 |
| `apply_environment_settings()` 遍历 `os.environ` 判断 | `_apply_environment_defaults()` 基于 `model_fields_set` |
| `validate_jwt_secret_key()` | 仍是同名私有方法，在 `_finalise()` 里调用 |
| `load_env_file()` 返回路径并加载 | `_resolve_env_file()` + 模块级 `load_dotenv` |

行为差异（都很小）：

- `CHECKPOINT_TABLES` 从实例属性改为 `ClassVar`，`settings.CHECKPOINT_TABLES` 照常可读，但不进 `model_dump()`。
- 值类型错误从"请求时 `ValueError`"提前到"导入时 `ValidationError`"。
- 启动日志文本由 `Environment.DEVELOPMENT` 改为 `development`。
- `Settings` 现在可以安全地多次实例化（旧版每次都会重复执行 `load_dotenv` 与校验），`get_settings()` 带缓存。

其余 50+ 个字段的默认值、解析规则、优先级与迁移前逐项比对一致。
