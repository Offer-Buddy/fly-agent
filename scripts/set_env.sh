#!/bin/bash

# =============================================================================
# 环境变量加载脚本
# =============================================================================
# 用法：source ./scripts/set_env.sh [development|staging|production]
#
# 作用：把 .env.$(ENV) 里的键值 export 到当前 shell，并打印一份配置摘要。
# Makefile 里所有带环境上下文的目标，都是先 source 本脚本再执行真正的命令。
#
# 为什么要 source 而不是直接执行：
#   只有 source（在同一进程内执行）才能把 export 的变量留给调用者的 shell；
#   用 ./set_env.sh 执行会在子进程里跑完就退出，变量全部丢失。
#   下面的第 6 行就是在检测这种误用。
# =============================================================================

# 检测是否被 source：BASH_SOURCE[0] 是脚本自身路径，$0 是当前 shell（或调用脚本）。
# 两者相同说明是「被当作程序执行」而不是被 source。
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    echo "错误：本脚本必须用 source 执行，不能直接运行。"
    echo "用法：source ./scripts/set_env.sh [development|staging|production]"
    exit 1
fi

# 终端着色（只在交互式终端有意义，重定向到文件时会写入转义字符）
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
RED='\033[0;31m'
PURPLE='\033[0;35m'
NC='\033[0m' # No Color / 恢复默认色

# 默认环境为开发环境；第一个位置参数可覆盖
ENV=${1:-development}

# 校验环境名白名单。注意这里不含 test：
# 测试环境由 pytest 的 fixture 自行构造 Settings，不需要 .env 文件。
if [[ ! "$ENV" =~ ^(development|staging|production)$ ]]; then
    echo -e "${RED}错误：环境名非法。可选 development、staging、production。${NC}"
    return 1
fi

# APP_ENV 是应用侧读取的环境标识（config.py 用它挑选默认值）
export APP_ENV=$ENV

# 校验 JWT 密钥强度：太短或仍是占位符都直接拒绝启动。
# 这是最后一道防线 —— config.py 里也有同样的检查，但那里只在
# Settings 实例化时触发；容器/脚本层面的失败信息更直观。
validate_jwt_secret_key() {
    local secret="$1"
    local secret_lc
    # 转小写后比对，避免 ChangeMe / CHANGEME 之类绕过
    secret_lc="$(printf '%s' "$secret" | tr '[:upper:]' '[:lower:]')"

    # 32 字符是 HS256 的最低安全要求
    if [ ${#secret} -lt 32 ]; then
        echo -e "${RED}错误：JWT_SECRET_KEY 长度必须不少于 32 个字符。${NC}"
        return 1
    fi

    # 常见示例值黑名单：这些值在网上公开，等于没有密钥
    case "$secret_lc" in
        "changeme"|"change_me"|"change_me_to_a_random_32_plus_char_string"|"supersecretkeythatshouldbechangedforproduction"|"your-jwt-secret-key")
            echo -e "${RED}错误：JWT_SECRET_KEY 仍是占位符/默认值，请换成随机串。${NC}"
            return 1
            ;;
    esac

    return 0
}

# 定位脚本所在目录与项目根目录。
# 因为被 source 时 $0 可能不是本脚本，这里优先用 BASH_SOURCE[0]，
# 并做了 -$0 的兜底，兼容少数不设置 BASH_SOURCE 的 shell。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 按环境拼接配置文件名，例如 .env.development
ENV_FILE="$PROJECT_ROOT/.env.$ENV"

if [ -f "$ENV_FILE" ]; then
    echo -e "${GREEN}正在从 $ENV_FILE 加载环境变量${NC}"

    # set -a 让后续所有赋值语句自动 export（不必逐行写 export），
    # source 完之后 set +a 关掉，避免影响调用者 shell 的后续行为。
    set -a
    source "$ENV_FILE"
    set +a

    echo -e "${GREEN}已成功加载 $ENV_FILE${NC}"
else
    # 开发环境允许「自动从模板生成」，降低新人上手成本
    if [[ "$ENV" == "development" ]]; then
        echo -e "${YELLOW}警告：$ENV_FILE 不存在，正从 .env.example 生成……${NC}"

        EXAMPLE_FILE="$PROJECT_ROOT/.env.example"
        if [ -f "$EXAMPLE_FILE" ]; then
            cp "$EXAMPLE_FILE" "$ENV_FILE"
            echo -e "${GREEN}已根据模板生成 $ENV_FILE。${NC}"
            echo -e "${PURPLE}启动前请先按需修改其中的配置。${NC}"

            # 生成后立即加载，保证本次 source 就能用
            set -a
            source "$ENV_FILE"
            set +a

            echo -e "${GREEN}已成功加载新生成的 $ENV_FILE${NC}"
        else
            echo -e "${RED}错误：模板文件不存在：$EXAMPLE_FILE${NC}"
            return 1
        fi
    else
        # 预发/生产绝不允许「用模板默认值凑合启动」，缺文件就直接失败
        echo -e "${RED}错误：$ENV_FILE 不存在。拒绝用模板默认值启动 $ENV 环境。${NC}"
        echo -e "${PURPLE}请先从 .env.example 复制出 $ENV_FILE，填好真实密钥后再试。${NC}"
        return 1
    fi
fi

# 密钥校验失败则中止整个流程（这里用 || return 1 而不是 exit，
# 因为脚本是被 source 的，exit 会把调用者的 shell 一起关掉）
validate_jwt_secret_key "${JWT_SECRET_KEY:-}" || return 1

# 交叉校验：请求的环境与文件里写的 APP_ENV 必须一致。
# 不一致说明加载了错的配置文件（比如 .env.production 里写着 APP_ENV=staging），
# 这种错配在小流量下很难被发现，必须提前拦住。
if [[ "$ENV" != "development" && "${APP_ENV:-}" != "$ENV" ]]; then
    echo -e "${RED}错误：请求的环境是 '$ENV'，但 APP_ENV 解析为 '${APP_ENV:-未设置}'。${NC}"
    echo -e "${PURPLE}请修正环境文件，使 APP_ENV 与请求的环境一致。${NC}"
    return 1
fi

# 打印配置摘要。这里只显示主机名/端口/模型名，不打印任何密钥。
echo -e "\n${GREEN}======= 环境摘要 =======${NC}"
echo -e "${GREEN}环境：          ${YELLOW}$ENV${NC}"
echo -e "${GREEN}项目根目录：    ${YELLOW}$PROJECT_ROOT${NC}"
echo -e "${GREEN}项目名称：      ${YELLOW}${PROJECT_NAME:-未设置}${NC}"
echo -e "${GREEN}API 版本：      ${YELLOW}${VERSION:-未设置}${NC}"

# 数据库相关做了双变量名兜底（POSTGRES_* 是当前命名，DB_* 是历史兼容）
echo -e "${GREEN}数据库主机：    ${YELLOW}${POSTGRES_HOST:-${DB_HOST:-未设置}}${NC}"
echo -e "${GREEN}数据库端口：    ${YELLOW}${POSTGRES_PORT:-${DB_PORT:-未设置}}${NC}"
echo -e "${GREEN}数据库名：      ${YELLOW}${POSTGRES_DB:-${DB_NAME:-未设置}}${NC}"
echo -e "${GREEN}数据库用户：    ${YELLOW}${POSTGRES_USER:-${DB_USER:-未设置}}${NC}"

echo -e "${GREEN}默认模型：      ${YELLOW}${DEFAULT_LLM_MODEL:-未设置}${NC}"
echo -e "${GREEN}日志级别：      ${YELLOW}${LOG_LEVEL:-未设置}${NC}"
echo -e "${GREEN}调试模式：      ${YELLOW}${DEBUG:-未设置}${NC}"

# 定义一个快捷启动函数，省去每次手敲长命令
start_app() {
    echo -e "${GREEN}正在以 $ENV 环境启动应用……${NC}"
    cd "$PROJECT_ROOT" && uvicorn app.main:app --reload --port 8000
}

# 把函数导出给当前 shell 使用：
#   bash 支持 export -f，直接导出即可
#   zsh 没有 export -f，只能就地重新定义一份
if [[ -n "$BASH_VERSION" ]]; then
    export -f start_app
elif [[ -n "$ZSH_VERSION" ]]; then
    function start_app() {
        echo -e "${GREEN}正在以 $ENV 环境启动应用……${NC}"
        cd "$PROJECT_ROOT" && uvicorn app.main:app --reload --port 8000
    }
else
    echo -e "${YELLOW}警告：当前 shell 不受支持，start_app 可能不可用。${NC}"
fi

# 输出可用命令提示
echo -e "\n${GREEN}可用命令：${NC}"
echo -e "  ${YELLOW}start_app${NC} - 以 $ENV 环境启动应用"

# 环境切换别名：注意 alias 只在交互式 shell 里生效，
# 在脚本里 source 本文件时别名不会注册，属正常现象。
alias dev_env="source '$SCRIPT_DIR/set_env.sh' development"
alias stage_env="source '$SCRIPT_DIR/set_env.sh' staging"
alias prod_env="source '$SCRIPT_DIR/set_env.sh' production"

echo -e "  ${YELLOW}dev_env${NC} - 切到开发环境"
echo -e "  ${YELLOW}stage_env${NC} - 切到预发环境"
echo -e "  ${YELLOW}prod_env${NC} - 切到生产环境"
