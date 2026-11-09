# =============================================================================
# 开发与运维命令入口
# =============================================================================
# 用法：
#   make                    # 等价于 make help，列出全部可用命令
#   make dev                # 本地热重载启动（默认 ENV=development）
#   make prod              # 以生产环境配置启动
#
# 三个全局约定：
#   ENV        环境标识，只能是 development / staging / production / test 之一。
#              凡是带环境上下文的命令，都会去读 .env.$(ENV) 这个文件。
#   DOCKER_COMPOSE
#              compose 命令名。新版 Docker 内置 `docker compose`（空格），
#              老版独立安装是 `docker-compose`（连字符）。本机不一样时可覆盖：
#              make docker-up DOCKER_COMPOSE="docker compose"
#   环境变量注入
#              统一走 scripts/set_env.sh：它把 .env.$(ENV) 里的键值 export 到
#              当前 shell，然后才执行目标命令。所以任何目标都不需要在命令行里
#              手动 `source`，也不会把密钥写进 Makefile。
# =============================================================================

# 不带参数直接敲 make 时执行的目标
.DEFAULT_GOAL := help

DOCKER_COMPOSE ?= docker-compose
# 默认环境：本地开发。`?=` 表示外部传了就以外部为准，没传才用这个值。
ENV            ?= development
# 合法环境白名单，供 check_env 校验用
VALID_ENVS     := development staging production test

# ---------------------------------------------------------------------------
# 内部辅助宏
# ---------------------------------------------------------------------------
# 校验 ENV 是否在白名单内；不合法就直接退出，避免后续命令读错 .env 文件
# 却不报错（那样排查起来很费时间）。grep -qw 按「整词」匹配，防止
# development 被 "dev" 之类的片段误命中。
define check_env
	@if ! echo "$(VALID_ENVS)" | grep -qw "$(ENV)"; then \
		echo "ENV=$(ENV) 非法。可选值：$(VALID_ENVS)"; exit 1; \
	fi
endef

# 校验 ENV 合法 + 确认对应的 .env.$(ENV) 真的存在
# 注意 $ENV_FILE 里写的是 $$ —— Makefile 中 shell 变量要转义成两个美元符，
# 否则会被 Make 当成自己的变量展开成空串。
define load_env_file
	$(call check_env)
	@ENV_FILE=.env.$(ENV); \
	if [ ! -f $$ENV_FILE ]; then \
		echo "找不到环境文件 $$ENV_FILE，请先创建（可从 .env.example 复制）"; exit 1; \
	fi
endef

# 简写：先加载环境变量，再执行传入的命令
# bash -c 起一个子 shell 而不是当前 shell，避免 set_env.sh 里 export 的变量
# 污染后续目标
run_with_env = bash -c "source scripts/set_env.sh $(ENV) && $(1)"

# ---------------------------------------------------------------------------
# 环境准备
# ---------------------------------------------------------------------------
# 安装 uv、按 uv.lock 同步依赖、安装 git 提交前钩子
# 新克隆仓库后第一件事就跑它
install:
	pip install uv
	uv sync
	uv run pre-commit install

# ---------------------------------------------------------------------------
# 启动服务
# ---------------------------------------------------------------------------
# 本地开发：--reload 监听文件变化自动重启，只监听本机 8000 端口
dev:
	@$(call run_with_env,uv run uvicorn app.main:app --reload --port 8000)

# 预发 / 生产：都是转到 _serve，靠 ENV 区分读哪个 .env 文件
staging:
	@$(call run_with_env,$(MAKE) _serve ENV=staging)

prod:
	@$(call run_with_env,$(MAKE) _serve ENV=production)

# 真正的生产启动命令（前缀下划线表示内部目标，不写进 help）
#   --host 0.0.0.0   容器/内网可访问
#   --loop uvloop    用 libuv 替代 asyncio 默认事件循环，吞吐更高（Windows 无此包，
#                    pyproject.toml 里已按平台条件排除，故只能在 Linux 用）
#   直接调 .venv/bin/python 而不是 uv run，省掉一层启动开销
_serve:
	@$(call run_with_env,./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop uvloop)

# ---------------------------------------------------------------------------
# 数据库迁移（Alembic）
# ---------------------------------------------------------------------------
# 把数据库升到最新版本（等价于 alembic upgrade head）
migrate:
	@$(call run_with_env,uv run alembic upgrade head)

# 根据模型的当前定义，自动生成一个新的迁移脚本
# 必须带说明，否则生成的文件名看不出这次改了什么
migration:
	@if [ -z "$(MSG)" ]; then \
		echo "用法：make migration MSG=\"描述本次改动\""; exit 1; \
	fi
	@$(call run_with_env,uv run alembic revision --autogenerate -m '$(MSG)')

# 回退最近一次迁移（只退一步，不会清空整库）
migrate-downgrade:
	@$(call run_with_env,uv run alembic downgrade -1)

# 打印迁移历史，含每条迁移的 revision 号与说明
migrate-history:
	@$(call run_with_env,uv run alembic history --verbose)

# ---------------------------------------------------------------------------
# 评测（evals 模块）
# ---------------------------------------------------------------------------
# 交互式跑评测，跑完会生成报告
eval:
	@$(call run_with_env,python -m evals.main --interactive)

# 用默认配置快速跑一轮，不逐项确认
eval-quick:
	@$(call run_with_env,python -m evals.main --quick)

# 只跑用例、不出报告，适合边改边验证
eval-no-report:
	@$(call run_with_env,python -m evals.main --no-report)

# ---------------------------------------------------------------------------
# 可观测性（Langfuse）
# ---------------------------------------------------------------------------
# 报表统计窗口，make trace-report HOURS=168 就是最近 7 天
HOURS ?= 24

# 链路自检：验「key 通了、数据到了、界面能看到」。不发真实模型请求，不花钱
trace-smoke:
	@$(call run_with_env,uv run python scripts/langfuse_smoke.py)

# 追踪报表：把最近一段时间的量、成本、延迟聚合成终端表格
trace-report:
	@$(call run_with_env,uv run python scripts/trace_report.py --hours $(HOURS))

# 自建 Langfuse 全栈（web + worker + postgres + clickhouse + redis + minio）
# 端口已避让本项目的 db(5432)、redis(6379) 与 Grafana(3000)，可与开发栈同时跑
langfuse-up:
	@$(DOCKER_COMPOSE) -f docker-compose.langfuse.yml up -d
	@echo "Langfuse 界面：http://localhost:3010（首次启动要等 1~2 分钟建表）"

langfuse-down:
	@$(DOCKER_COMPOSE) -f docker-compose.langfuse.yml down

langfuse-logs:
	@$(DOCKER_COMPOSE) -f docker-compose.langfuse.yml logs -f langfuse-web langfuse-worker

# ---------------------------------------------------------------------------
# 代码质量
# ---------------------------------------------------------------------------
# ruff check 只检查不修改；配合 `make format` 或 `make pre-commit` 才自动修
lint:
	uv run ruff check .

# ruff format 改文件，注意它会直接写盘
format:
	uv run ruff format .

# pyright 静态类型检查，配置见 pyproject.toml 的 [tool.pyright]
typecheck:
	uv run pyright

# 提交前自检的常用组合
check: lint typecheck
	@echo "全部检查通过"

# 对全仓库跑一遍 pre-commit 钩子（不只针对暂存区改动）
pre-commit:
	uv run pre-commit run --all-files

# 配置三处一致性自查：config.py 字段 ↔ .env.example ↔ docs/configuration.md
# 有差异时退出码为 1，适合挂进 CI 或 pre-commit
settings-check:
	uv run python scripts/gen_settings_docs.py

# 升级钩子版本号（会改写 .pre-commit-config.yaml 里的 rev）
pre-commit-update:
	uv run pre-commit autoupdate

# ---------------------------------------------------------------------------
# Docker —— 最小组合（API + 数据库）
# ---------------------------------------------------------------------------
docker-build:
	$(call check_env)
	@./scripts/build-docker.sh $(ENV)

# 起 db + app 两个容器；--env-file 让 compose 能解析 ${POSTGRES_USER} 这类变量，
# APP_ENV 传给 compose，用于挑 .env.${APP_ENV} 与构建参数
docker-up:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) up -d --build db app

docker-down:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) down

# -f 跟随输出，Ctrl+C 只退出日志不影响容器
docker-logs:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) logs -f app db

# 在运行中的 app 容器里跑迁移。为什么要在容器里跑：
# 数据库主机名 "db" 是 compose 内部 DNS，宿主机上解析不了。
# 前提：栈已启动（先 make docker-up）。
docker-migrate:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) exec -T app /app/.venv/bin/alembic upgrade head

# 在运行中的 app 容器里回退最近一次迁移
docker-migrate-downgrade:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) exec -T app /app/.venv/bin/alembic downgrade -1

# 在运行中的 app 容器里查看迁移历史
docker-migrate-history:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) exec -T app /app/.venv/bin/alembic history --verbose

# ---------------------------------------------------------------------------
# Docker —— 全栈（API + 数据库 + Prometheus + Grafana + cadvisor）
# ---------------------------------------------------------------------------
# up -d 不带服务名，表示启动 docker-compose.yml 里的全部服务
stack-up:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) up -d

stack-down:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) down

stack-logs:
	$(call load_env_file)
	@APP_ENV=$(ENV) $(DOCKER_COMPOSE) --env-file .env.$(ENV) logs -f

# ---------------------------------------------------------------------------
# 杂项
# ---------------------------------------------------------------------------
# 清理本地虚拟环境与各种缓存；不会动数据库和 .env 文件。
# 删掉 .venv 后要重新 make install。
clean:
	rm -rf .venv __pycache__ .pytest_cache

# ---------------------------------------------------------------------------
# 帮助
# ---------------------------------------------------------------------------
help:
	@echo "用法：make <目标> [ENV=development|staging|production|test]"
	@echo ""
	@echo "环境准备："
	@echo "  install                  安装依赖并装好 pre-commit 钩子"
	@echo ""
	@echo "启动服务："
	@echo "  dev                      开发服务器（热重载，端口 8000）"
	@echo "  staging                  预发服务器"
	@echo "  prod                     生产服务器"
	@echo ""
	@echo "数据库："
	@echo "  migrate                  迁移到最新版本（默认 ENV=development）"
	@echo "  migration MSG=...        根据模型改动生成迁移脚本"
	@echo "  migrate-downgrade        回退最近一次迁移"
	@echo "  migrate-history          查看迁移历史"
	@echo ""
	@echo "评测："
	@echo "  eval                     交互式跑评测"
	@echo "  eval-quick               用默认配置跑评测"
	@echo "  eval-no-report           只跑用例、不出报告"
	@echo ""
	@echo "可观测性（Langfuse）："
	@echo "  trace-smoke              链路自检，确认数据能上报"
	@echo "  trace-report             追踪报表（HOURS=24 可改窗口）"
	@echo "  langfuse-up              起一套自建 Langfuse（端口 3010）"
	@echo "  langfuse-down            停掉自建 Langfuse"
	@echo "  langfuse-logs            查看自建 Langfuse 日志"
	@echo ""
	@echo "代码质量："
	@echo "  lint                     Ruff 静态检查"
	@echo "  format                   Ruff 格式化"
	@echo "  typecheck                Pyright 类型检查"
	@echo "  check                    依次跑 lint + typecheck"
	@echo "  pre-commit               对全仓库跑 pre-commit 钩子"
	@echo "  pre-commit-update        升级钩子版本"
	@echo "  settings-check           配置三处一致性自查（代码/模板/文档）"
	@echo ""
	@echo "Docker（API + 数据库）："
	@echo "  docker-build             构建镜像"
	@echo "  docker-up                启动 API 与数据库容器"
	@echo "  docker-down              停止容器"
	@echo "  docker-logs              持续查看容器日志"
	@echo "  docker-migrate           在 app 容器内执行迁移"
	@echo "  docker-migrate-downgrade 在 app 容器内回退一次迁移"
	@echo "  docker-migrate-history   在 app 容器内查看迁移历史"
	@echo ""
	@echo "Docker（全栈，含 Prometheus + Grafana）："
	@echo "  stack-up                 启动全部服务"
	@echo "  stack-down               停止全部服务"
	@echo "  stack-logs               持续查看全部服务日志"
	@echo ""
	@echo "杂项："
	@echo "  clean                    删除 .venv、__pycache__、.pytest_cache"

# 把所有目标声明为「伪目标」：即使目录里恰好有同名文件，也照样执行配方
.PHONY: install dev staging prod _serve \
        migrate migration migrate-downgrade migrate-history \
        eval eval-quick eval-no-report \
        trace-smoke trace-report langfuse-up langfuse-down langfuse-logs \
        lint format typecheck check pre-commit pre-commit-update settings-check \
        docker-build docker-up docker-down docker-logs docker-migrate \
        docker-migrate-downgrade docker-migrate-history \
        stack-up stack-down stack-logs \
        clean help
