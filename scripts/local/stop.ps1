# 一键停止本地开发栈：8000 端口上的 FastAPI 应用 + 5432 端口上的 PostgreSQL。
#
# 编码说明：本文件以 UTF-8 with BOM 保存，否则 Windows PowerShell 5.1 会按
# 系统 ANSI（中文系统为 GBK）解码，中文提示会变成乱码。

# 这里刻意用 Continue 而不是 Stop：
# 停止过程本来就是「尽力而为」，某一项停不掉（权限不足、进程已退出）
# 也应当继续把剩下的停完，而不是中途抛异常中断。
$ErrorActionPreference = "Continue"

$pgHome = if ($env:FLY_PG_HOME) { $env:FLY_PG_HOME } else { Join-Path $env:LOCALAPPDATA "fly-agent" }
$pgBin = Join-Path $pgHome "pgsql\bin"
$pgData = Join-Path $pgHome "pgdata"

Write-Host "== 正在停止 Fly Agent 本地开发栈 ==" -ForegroundColor Cyan

# ---- 1/2 停应用 ----
# 按「谁在监听 8000」找进程，比按进程名杀更准确：
# uvicorn 实际进程名可能是 python.exe，容易被同名进程误伤。
$conns = Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue
if ($conns) {
    # OwningProcess 可能重复（多网卡监听同一端口），去重后再逐个处理
    foreach ($pid8000 in ($conns | Select-Object -ExpandProperty OwningProcess -Unique)) {
        try {
            Stop-Process -Id $pid8000 -Force -ErrorAction Stop
            Write-Host "[1/2] API 已停止（pid $pid8000）" -ForegroundColor Green
        } catch {
            Write-Host "[!] 无法停止 pid $pid8000：$($_.Exception.Message)" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "[1/2] API 本来就没在运行" -ForegroundColor Gray
}

# ---- 2/2 停数据库 ----
# 先 pg_ctl status 判断是否在跑：直接 stop 一个没启动的实例会报错刷屏。
if (Test-Path (Join-Path $pgBin "pg_ctl.exe")) {
    $status = & (Join-Path $pgBin "pg_ctl.exe") -D $pgData status 2>&1
    if ($LASTEXITCODE -eq 0) {
        # -m fast：断开现有连接后立即关闭并做一次检查点，不长时间等待客户端退出；
        # -w 等待关闭完成再返回，保证脚本结束时端口已释放。
        & (Join-Path $pgBin "pg_ctl.exe") -D $pgData -m fast -w stop | Out-Null
        Write-Host "[2/2] PostgreSQL 已停止" -ForegroundColor Green
    } else {
        Write-Host "[2/2] PostgreSQL 本来就没在运行" -ForegroundColor Gray
    }
} else {
    Write-Host "[2/2] 未找到 PostgreSQL 可执行文件，跳过" -ForegroundColor Gray
}

Write-Host "完成。" -ForegroundColor Cyan
