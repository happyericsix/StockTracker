# start-standalone.ps1 —— 一键启动 qq_standalone.py
# 用法：.\start-standalone.ps1

Set-Location -Path $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Stock Tracker · qq_standalone" -ForegroundColor Cyan
Write-Host " (QQ 收发客户端, 端口 3003)" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 激活虚拟环境
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-Host "[1/2] 激活虚拟环境..." -ForegroundColor Yellow
    & .\.venv\Scripts\Activate.ps1
} else {
    Write-Host "[1/2] 虚拟环境不存在: .venv" -ForegroundColor Red
    pause
    exit 1
}

# 检查依赖
$aiocqMissing = & python -c "import aiocqhttp" 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[警告] 缺少依赖 aiocqhttp" -ForegroundColor Red
    Write-Host "      正在安装: pip install aiocqhttp" -ForegroundColor Yellow
    pip install aiocqhttp
}

# 启动
Write-Host ""
Write-Host "[2/2] 启动 qq_standalone.py (等待 NapCat 连接 :3003)..." -ForegroundColor Yellow
Write-Host "      按 Ctrl+C 停止" -ForegroundColor DarkGray
Write-Host ""

python qq_standalone.py
