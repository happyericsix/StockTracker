# start-all.ps1 —— 一键启动 Stock Tracker 全栈（开发模式）
#
# 为什么需要这个脚本：原来的 start.ps1 名字像是"启动全部"，其实只启动 Python 数据服务。
# 而机器人（智能助手）需要三个服务同时在：
#     浏览器 → Java(:8080) → Python(:8000) → DeepSeek
# 只起 Python 的话，前端那个 POST 打到没启动的 Java，直接失败。
#
# 用法：
#     .\start-all.ps1            # 启动全部（已启动的会跳过）
#     .\start-all.ps1 -Stop      # 停止全部
#     .\start-all.ps1 -Status    # 只看状态
#
# 拓扑说明（与 application.properties / python .env 对齐）：
#     MySQL  3306  走后端配置里的 localhost:3306/stockdb（宿主机原生 MySQL，不是容器）
#     Redis  6379  走 Docker 容器 stock-redis（compose 里没发布端口，这里用 docker run 的映射）
#     Python 8000  uvicorn（读 python-data-service/.env，含 DEEPSEEK_API_KEY）
#     Java   8080  Spring Boot
#     前端   5173  Vite（/api 代理到 :8080）

param(
    [switch]$Stop,
    [switch]$Status
)

$ErrorActionPreference = 'Continue'
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PyDir = Join-Path $Root 'python-data-service'
$FeDir = Join-Path $Root 'frontend'
$LogDir = Join-Path $Root 'logs'
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Step($msg) { Write-Host "[*] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "    OK  $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "    !!  $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "    XX  $msg" -ForegroundColor Red }

function Test-Port([int]$Port) {
    return [bool](Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Wait-Port([int]$Port, [int]$TimeoutSec, [string]$Name) {
    for ($i = 1; $i -le $TimeoutSec; $i++) {
        if (Test-Port $Port) { Write-Ok "$Name 已就绪 (:${Port})，用时约 ${i}s"; return $true }
        Start-Sleep -Seconds 1
    }
    Write-Err "$Name 在 ${TimeoutSec}s 内未就绪 (:${Port})"
    return $false
}

function Show-Status {
    Write-Host ''
    Write-Host '  ── 服务状态 ──────────────────────────────' -ForegroundColor White
    $svc = @(
        @{ Port = 3306; Name = 'MySQL (原生)' },
        @{ Port = 6379; Name = 'Redis (Docker)' },
        @{ Port = 8000; Name = 'Python 数据服务' },
        @{ Port = 8080; Name = 'Java 后端' },
        @{ Port = 5173; Name = '前端 Vite' }
    )
    foreach ($s in $svc) {
        $on = Test-Port $s.Port
        $mark = if ($on) { '在线' } else { '离线' }
        $color = if ($on) { 'Green' } else { 'DarkGray' }
        Write-Host ("    {0,-6} {1,-16} {2}" -f $s.Port, $s.Name, $mark) -ForegroundColor $color
    }
    Write-Host '  ──────────────────────────────────────────' -ForegroundColor White
    Write-Host ''
    if (Test-Port 8000) {
        try {
            $h = (Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -TimeoutSec 5 -UseBasicParsing).Content
            Write-Host "    机器人 LLM 就绪: $h"
        } catch { Write-Warn 'Python /health 取不到' }
    }
    Write-Host '    入口: 前端 http://localhost:5173  |  Java http://localhost:8080  |  Python http://127.0.0.1:8000/docs'
    Write-Host ''
}

if ($Status) { Show-Status; exit 0 }

# ============================ 停止 ============================
if ($Stop) {
    Write-Step '停止服务'
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*uvicorn*app:app*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Ok "停止 uvicorn (PID $($_.ProcessId))" }

    Get-CimInstance Win32_Process -Filter "Name='java.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*stocktracker*' -or $_.CommandLine -like '*spring-boot:run*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Ok "停止 Java (PID $($_.ProcessId))" }

    Get-CimInstance Win32_Process -Filter "Name='node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -like '*vite*' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Ok "停止 Vite (PID $($_.ProcessId))" }

    Write-Host ''
    Write-Warn 'Redis 容器仍在运行；要停用: docker stop stock-redis'
    Show-Status
    exit 0
}

# ============================ 启动 ============================
Write-Host ''
Write-Host '  Stock Tracker 全栈启动（开发模式）' -ForegroundColor White
Write-Host '  ==================================' -ForegroundColor White

# ---------- 1. MySQL（原生） ----------
Write-Step '1/5 检查 MySQL (3306)'
if (Test-Port 3306) {
    Write-Ok '3306 已在监听（宿主机原生 MySQL，application.properties 指向 localhost:3306/stockdb）'
} else {
    Write-Err '3306 没有监听。后端连不上数据库会启动失败。'
    Write-Warn '请先启动你的 MySQL 服务；或改用 docker compose 的 mysql（注意它不发布宿主端口，需自行调整配置）'
    Write-Warn '注意：不要用 README 里的 docker run -p 3306:3306，会和原生 MySQL 冲突'
}

# ---------- 2. Redis（Docker） ----------
Write-Step '2/5 检查 Redis (6379)'
if (Test-Port 6379) {
    Write-Ok '6379 已在监听'
} else {
    $dockerOk = $false
    try { & docker version --format '{{.Server.Version}}' 2>$null | Out-Null; $dockerOk = ($LASTEXITCODE -eq 0) } catch {}
    if (-not $dockerOk) {
        Write-Warn 'Docker 守护进程没在跑，尝试启动 Docker Desktop…'
        $dd = @(
            "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe",
            "${env:ProgramFiles(x86)}\Docker\Docker\Docker Desktop.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
        if ($dd) {
            Start-Process -FilePath $dd | Out-Null
            for ($i = 1; $i -le 60; $i++) {
                Start-Sleep -Seconds 3
                try { & docker version --format '{{.Server.Version}}' 2>$null | Out-Null; if ($LASTEXITCODE -eq 0) { $dockerOk = $true; break } } catch {}
            }
        }
        if (-not $dockerOk) { Write-Err 'Docker 仍未就绪，无法启动 Redis' }
    }
    if ($dockerOk) {
        $exists = & docker ps -a --filter 'name=^stock-redis$' --format '{{.Names}}' 2>$null
        if ($exists) { & docker start stock-redis 2>&1 | Out-Null }
        else { & docker run -d --name stock-redis -p 6379:6379 redis:7-alpine 2>&1 | Out-Null }
        Wait-Port 6379 30 'Redis' | Out-Null
        & docker exec stock-redis redis-cli ping 2>&1 | ForEach-Object { if ($_ -match 'PONG') { Write-Ok 'Redis PONG' } }
    }
}

# ---------- 3. Python ----------
Write-Step '3/5 启动 Python 数据服务 (8000)'
if (Test-Port 8000) {
    Write-Ok '8000 已在线，跳过'
} else {
    $py = Join-Path $PyDir '.venv\Scripts\python.exe'
    if (-not (Test-Path $py)) { Write-Err "找不到 $py" }
    else {
        $envFile = Join-Path $PyDir '.env'
        if (Test-Path $envFile) { Write-Ok '.env 存在（含 DEEPSEEK_API_KEY）' } else { Write-Warn '.env 不存在 → 机器人只能给模板回复' }
        $env:PYTHONIOENCODING = 'utf-8'
        Start-Process -FilePath $py -ArgumentList '-m','uvicorn','app:app','--host','127.0.0.1','--port','8000' `
            -WorkingDirectory $PyDir -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $LogDir 'python.out.log') `
            -RedirectStandardError  (Join-Path $LogDir 'python.err.log')
        Wait-Port 8000 60 'Python' | Out-Null
    }
}

# ---------- 4. Java ----------
Write-Step '4/5 启动 Java 后端 (8080)'
if (Test-Port 8080) {
    Write-Ok '8080 已在线，跳过'
} else {
    Start-Process -FilePath (Join-Path $Root 'mvnw.cmd') -ArgumentList 'spring-boot:run' `
        -WorkingDirectory $Root -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $LogDir 'java.out.log') `
        -RedirectStandardError  (Join-Path $LogDir 'java.err.log')
    Write-Warn '首次启动要编译 + 等 Spring 上下文，约 20-40 秒'
    Wait-Port 8080 150 'Java' | Out-Null
}

# ---------- 5. 前端 ----------
Write-Step '5/5 启动前端 Vite (5173)'
if (Test-Port 5173) {
    Write-Ok '5173 已在线，跳过'
} else {
    $npm = (Get-Command npm.cmd -ErrorAction SilentlyContinue).Source
    if (-not $npm) { Write-Err '找不到 npm.cmd' }
    else {
        Start-Process -FilePath $npm -ArgumentList 'run','dev' `
            -WorkingDirectory $FeDir -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $LogDir 'frontend.out.log') `
            -RedirectStandardError  (Join-Path $LogDir 'frontend.err.log')
        Wait-Port 5173 60 '前端 Vite' | Out-Null
    }
}

Show-Status

Write-Host '  机器人（智能助手）就绪判据：' -ForegroundColor White
Write-Host '    1) 三个服务都在（Java 8080 + Python 8000 + 前端 5173）'
Write-Host '    2) GET http://127.0.0.1:8000/health 里 llm_available = true'
Write-Host '    3) 打开 http://localhost:5173 → 登录 → 智能助手'
Write-Host ''
Write-Host "  日志目录: $LogDir" -ForegroundColor DarkGray
Write-Host '  停止: .\start-all.ps1 -Stop      状态: .\start-all.ps1 -Status' -ForegroundColor DarkGray
Write-Host ''
