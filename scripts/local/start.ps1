# 一键启动本地开发栈：便携版 PostgreSQL 17 + pgvector，然后拉起 FastAPI 应用。
# 幂等：已经在跑的服务会被跳过，重复执行不会出错。
#
# 编码说明：本文件以 UTF-8 with BOM 保存。Windows PowerShell 5.1 在读取无 BOM 的
# 脚本时会按系统 ANSI（中文系统为 GBK）解码，中文会变成乱码；带 BOM 才能正确识别。

$ErrorActionPreference = "Stop"

# 项目根目录：本脚本位于 <root>/scripts/local/，所以向上推两级
$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# PostgreSQL 安装根目录：优先用环境变量 FLY_PG_HOME，否则默认放 LOCALAPPDATA\fly-agent
# （便携版免安装，解压出来的 pgsql\ 与数据目录 pgdata\ 都放在这里）
$pgHome = if ($env:FLY_PG_HOME) { $env:FLY_PG_HOME } else { Join-Path $env:LOCALAPPDATA "fly-agent" }
$pgBin = Join-Path $pgHome "pgsql\bin"
$pgData = Join-Path $pgHome "pgdata"
$pgLog = Join-Path $pgHome "pg.log"
$python = Join-Path $root ".venv\Scripts\python.exe"
$envFile = Join-Path $root ".env.development"

# 探测某端口是否已有进程在监听。
# 用 Get-NetTCPConnection 的 Listen 状态判断，比 Test-NetConnection 快得多
# （后者会真的发起一次连接，超时等待很浪费时间）。
function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

Write-Host "== Fly Agent 本地开发栈 ==" -ForegroundColor Cyan

# ---- 前置检查：缺任何一项都在动手之前失败，不留下半启动状态 ----
if (-not (Test-Path $envFile)) {
    Write-Host "[x] 找不到 .env.development，请先从 .env.example 复制一份。" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path $python)) {
    Write-Host "[x] 找不到 .venv（虚拟环境未创建），请先执行：uv sync" -ForegroundColor Red
    exit 1
}
if (-not (Test-Path (Join-Path $pgBin "pg_ctl.exe"))) {
    Write-Host "[x] 在 $pgBin 下找不到 PostgreSQL" -ForegroundColor Red
    Write-Host "    请把 FLY_PG_HOME 指向同时包含 pgsql\ 与 pgdata\ 的目录" -ForegroundColor Yellow
    exit 1
}

# ---- 1/3 数据库 ----
if (Test-Port 5432) {
    Write-Host "[1/3] PostgreSQL 已在 5432 端口运行，跳过" -ForegroundColor Green
} else {
    Write-Host "[1/3] 正在启动 PostgreSQL……" -ForegroundColor Yellow
    # 处理「上次异常退出留下的 postmaster.pid」：
    # 如果 pid 文件里记录的进程已经不存在，pg_ctl 会误判实例仍在运行而拒绝启动，
    # 这里先检测再把残留文件删掉。
    $pidFile = Join-Path $pgData "postmaster.pid"
    if (Test-Path $pidFile) {
        $stalePid = (Get-Content $pidFile -First 1) -as [int]
        if ($stalePid -and -not (Get-Process -Id $stalePid -ErrorAction SilentlyContinue)) {
            Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
        }
    }
    # -w 表示等待启动完成，-t 30 最多等 30 秒；输出重定向丢掉，避免刷屏
    & (Join-Path $pgBin "pg_ctl.exe") -D $pgData -l $pgLog -o "-p 5432" -w -t 30 start | Out-Null
    # 再自己轮询一遍端口：-w 偶发提前返回，用实际可连接作为最终判据
    $deadline = (Get-Date).AddSeconds(30)
    while (-not (Test-Port 5432) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 500 }
    if (-not (Test-Port 5432)) {
        Write-Host "[x] PostgreSQL 启动失败，详见日志：$pgLog" -ForegroundColor Red
        exit 1
    }
    Write-Host "      PostgreSQL 已就绪" -ForegroundColor Green
}

# ---- 2/3 应用 ----
if (Test-Port 8000) {
    Write-Host "[2/3] API 已在 8000 端口运行，跳过" -ForegroundColor Green
} else {
    Write-Host "[2/3] 正在启动 API……" -ForegroundColor Yellow
    # Windows 上必须带 --reload：uvicorn 的重载器会选用 selector 事件循环，
    # 而 psycopg 的异步连接在 Windows 默认的 Proactor 事件循环下会有兼容问题。
    # 新开一个 cmd 窗口运行（-WindowStyle Normal），方便随时看应用日志与中断。
    $cmd = "cd /d `"$root`" && `"$python`" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000"
    Start-Process -FilePath "cmd.exe" -ArgumentList "/k", $cmd -WindowStyle Normal

    # 启动较慢（要初始化数据库、加载模型注册表、编译 LangGraph），最多等 60 秒；
    # 以 /health 返回 200 作为「真的可用」的判据，而不是只看端口是否 listening。
    $deadline = (Get-Date).AddSeconds(60)
    $ready = $false
    while (-not $ready -and (Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        try {
            $resp = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 5
            $ready = $resp.StatusCode -eq 200
        } catch { }
    }
    if (-not $ready) {
        Write-Host "[x] API 未在预期时间内就绪，请查看标题为 'Fly Agent' 的窗口" -ForegroundColor Red
        exit 1
    }
    Write-Host "      API 已就绪" -ForegroundColor Green
}

# ---- 3/3 收尾 ----
Write-Host "[3/3] 正在打开 API 文档……" -ForegroundColor Yellow
Start-Process "http://127.0.0.1:8000/docs"

Write-Host ""
Write-Host "API 地址 ： http://127.0.0.1:8000" -ForegroundColor Cyan
Write-Host "接口文档 ： http://127.0.0.1:8000/docs" -ForegroundColor Cyan
Write-Host "数据库   ： 127.0.0.1:5432" -ForegroundColor Cyan
Write-Host ""
Write-Host "停止全部服务请执行 stop-local.bat" -ForegroundColor Gray
