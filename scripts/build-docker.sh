#!/bin/bash
# =============================================================================
# 构建 Docker 镜像（带脱敏输出）
# =============================================================================
# 用法：./scripts/build-docker.sh <environment>
#       make docker-build ENV=production（会先校验 ENV 合法性）
#
# 为什么不直接在命令行敲 docker build：
#   1. 先加载 .env.$ENV，保证构建参数与环境一致
#   2. 打印配置摘要时把主机名/库名/密钥全部打成 ********，
#      避免终端历史、CI 日志里留下真实的连接信息（终端里滚过的密钥等于泄露）
# =============================================================================
set -e

# 参数个数必须正好是 1
if [ $# -ne 1 ]; then
    echo "用法：$0 <环境>"
    echo "可选环境：development、staging、production"
    exit 1
fi

ENV=$1

# 校验环境名，防止拼错后静默使用空的 env 文件
if [[ ! "$ENV" =~ ^(development|staging|production)$ ]]; then
    echo "环境名非法。可选：development、staging、production"
    exit 1
fi

echo "正在为 $ENV 环境构建 Docker 镜像"

# 缺配置文件时自动从模板复制（仅提醒修改，不会终止流程）
ENV_FILE=".env.$ENV"
if [ ! -f "$ENV_FILE" ]; then
    echo "警告：$ENV_FILE 不存在，正从 .env.example 生成"
    if [ ! -f .env.example ]; then
        echo "错误：.env.example 不存在"
        exit 1
    fi
    cp .env.example "$ENV_FILE"
    echo "请先修改 $ENV_FILE 中的配置，再启动容器"
fi

echo "正在从 $ENV_FILE 加载环境变量（敏感值已脱敏）"

# set -a 让 source 时所有赋值自动 export，source 完再关掉
set -a
source "$ENV_FILE"
set +a

echo "环境：$ENV"

# 脱敏助手：有值就显示 ********，没值就显示「未设置」。
# 这样既能确认「变量确实传进来了」，又不会把值写进日志。
mask_env() {
    local value="$1"
    if [ -z "$value" ]; then
        echo "未设置"
    else
        echo "********"
    fi
}

echo "环境：$ENV"
# 数据库连接信息同样脱敏，而不是直接打印 —— 主机名与库名本身也是探测线索
echo "数据库主机：$(mask_env "${POSTGRES_HOST:-${DB_HOST:-}}")"
echo "数据库端口：$(mask_env "${POSTGRES_PORT:-${DB_PORT:-}}")"
echo "数据库名：$(mask_env "${POSTGRES_DB:-${DB_NAME:-}}")"
echo "数据库用户：$(mask_env "${POSTGRES_USER:-${DB_USER:-}}")"
echo "API 密钥：********（已脱敏）"

# 构建镜像。
# --no-cache：每次都从头构建，保证「依赖不会被旧缓存粘住」。
#   代价是慢很多；日常迭代可以去掉，只在发版或排查「依赖版本不对」时保留。
# 下面这几个 --build-arg 需要说明：Dockerfile 里目前只声明了 ARG APP_ENV，
# 其余几个没有被声明，Docker 会报警告并忽略它们 —— 也就是说密钥并没有真的
# 进入构建上下文，而是运行时由 env_file / environment 注入。这是当前有意为之
# 的形态（密钥不烤进镜像层）；如果将来确实需要在构建期使用，必须先到
# Dockerfile 里补对应的 ARG 声明。
docker build --no-cache \
    --build-arg APP_ENV="$ENV" \
    --build-arg OPENAI_API_KEY="$OPENAI_API_KEY" \
    --build-arg LANGFUSE_PUBLIC_KEY="$LANGFUSE_PUBLIC_KEY" \
    --build-arg LANGFUSE_SECRET_KEY="$LANGFUSE_SECRET_KEY" \
    --build-arg JWT_SECRET_KEY="$JWT_SECRET_KEY" \
    -t fastapi-langgraph-template:"$ENV" .

echo "镜像 fastapi-langgraph-template:$ENV 构建完成"
