# =============================================================================
# 生产镜像构建脚本
# =============================================================================
# 构建：
#   docker build -t fly-agent:latest .
#   ./scripts/build-docker.sh production      # 走脚本，会自动加载 .env.production
#   或 make docker-build ENV=production
#
# 整体思路（三层缓存，重建才够快）：
#   1. 系统依赖层：apt 装编译器与头文件，很少变
#   2. Python 依赖层：只拷贝 pyproject.toml + uv.lock 就装依赖，
#      所以改业务代码不会触发重装依赖
#   3. 应用层：最后才拷贝源码
# =============================================================================

# 基础镜像用 slim 版而非 alpine：
#   - alpine 用 musl libc，psycopg2 / uvloop / bcrypt 这些带 C 扩展的包
#     要么没有预编译轮子、要么得现场编译，反而更大更慢
#   - slim 是 Debian 精简版，glibc 生态完好，轮子直接装
FROM python:3.13.2-slim

# 容器内工作目录；后面的 COPY / RUN / CMD 相对路径都以它为基准
WORKDIR /app

# ---------------------------------------------------------------------------
# 构建参数与环境变量
# ---------------------------------------------------------------------------
# ARG 只在构建期可见（不会留在最终镜像的 ENV 里），
# 作用是让 --build-arg APP_ENV=xxx 能落到下面 ENV 的默认值上
ARG APP_ENV=production

# 说明（Dockerfile 的 ENV 续行里不能写注释，只能统一写在这里）：
#   APP_ENV               运行环境标识；app/core/config.py 据此挑选该环境的默认值
#   PYTHONFAULTHANDLER=1  Python 段错误时打印 C 层调用栈，定位原生扩展崩溃必需
#   PYTHONUNBUFFERED=1    不缓冲 stdout/stderr，否则 docker logs 要等缓冲满才看得到
#   PYTHONHASHSEED=random 打散字符串哈希种子，防止被构造的哈希碰撞拖成 O(n²)
#   PIP_NO_CACHE_DIR=1    不留 pip 缓存，直接减小镜像体积
#   PIP_DISABLE_PIP_VERSION_CHECK=on
#                         不检查 pip 自身新版本，少一次网络往返
#   PIP_DEFAULT_TIMEOUT=100
#                         下载超时放宽到 100 秒，避免构建在网络抖动时偶发失败
ENV APP_ENV=${APP_ENV} \
    PYTHONFAULTHANDLER=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONHASHSEED=random \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=on \
    PIP_DEFAULT_TIMEOUT=100

# ---------------------------------------------------------------------------
# 系统依赖
# ---------------------------------------------------------------------------
#   build-essential  gcc/make；装 uvloop、bcrypt 等需要现场编译的包时用得上
#   libpq-dev        PostgreSQL 客户端头文件，psycopg2 编译依赖
# 装完立刻删掉 /var/lib/apt/lists，否则这些索引会白白占掉几十 MB
# （必须和 apt-get install 写在同一个 RUN 里，否则删除不会减小镜像层）
RUN apt-get update && apt-get install -y \
    build-essential \
    libpq-dev \
    && pip install --upgrade pip \
    && pip install uv \
    && rm -rf /var/lib/apt/lists/*

# ---------------------------------------------------------------------------
# Python 依赖
# ---------------------------------------------------------------------------
# 先只拷贝依赖清单，跑一次「只装依赖、不装本项目」。
# 这一层能否命中缓存，取决于 pyproject.toml / uv.lock 有没有变——
# 只改 app/ 下的代码时缓存命中，重建通常只要几秒。
# --frozen 表示严格按 uv.lock 安装，不会顺手升级版本，保证镜像可复现。
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project

# 再拷贝全部源码，把本项目自身也按锁文件装进 /app/.venv。
# .dockerignore 负责把 .venv、__pycache__、.git、日志、文档等排除在外，
# 否则会把宿主机的虚拟环境一起塞进镜像。
COPY . .
RUN uv sync --frozen

# ---------------------------------------------------------------------------
# 运行身份与目录
# ---------------------------------------------------------------------------
# chmod 必须放在切用户之前：切到 appuser 之后就没有权限改文件属性了
RUN chmod +x /app/scripts/docker-entrypoint.sh

# 建非 root 用户并以它运行，缩小容器被攻破后的影响面；
# 顺手把 /app 的属主整体改成 appuser，后面才能写 .venv 与 logs
RUN useradd -m appuser && chown -R appuser:appuser /app
USER appuser

# 应用会把结构化日志写成 JSONL 文件落到 /app/logs；
# 目录必须在切用户之后由当前用户创建，否则属主是 root，运行时会写不进去。
# docker-compose.yml 里又把宿主机 ./logs 挂到了这个位置，方便直接看日志。
RUN mkdir -p /app/logs

# 声明容器监听 8000 端口（仅作文档与链接提示，不改变实际监听行为）
EXPOSE 8000

# 构建时打印一次当前环境，仅用于确认 --build-arg APP_ENV 是否生效
RUN echo "Using ${APP_ENV} environment"

# ---------------------------------------------------------------------------
# 启动
# ---------------------------------------------------------------------------
# ENTRYPOINT 是入口脚本，负责：
#   1. 读取 .env.${APP_ENV}（环境变量已存在时以环境变量为准，不覆盖）
#   2. 校验 JWT_SECRET_KEY、OPENAI_API_KEY 是否存在，缺了直接退出
#   3. 打印一份脱敏后的最终配置摘要，再 exec 交给 CMD
# 注意：迁移不在这里自动执行，避免多副本同时启动时互相抢锁；
# 需要时显式跑 make docker-migrate。
#
# CMD 用 exec 形式（JSON 数组）：进程直接作为 PID 1，能正常收到 SIGTERM，
# docker stop 才走优雅关闭而不是等 10 秒后强杀。
# 用 .venv/bin/uvicorn 绝对路径，避免 PATH 里混进系统级同名命令。
# 生产不开 --reload（热重载会常驻文件监听，且会拖慢启动）。
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["/app/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
