# start.ps1 —— 一键启动 Python 数据服务
# 用法：在 PowerShell 里执行  .\start.ps1

# 切到脚本所在目录
Set-Location -Path $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Stock Tracker · Python Data Service" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 激活虚拟环境（如果存在）
if (Test-Path ".\.venv\Scripts\Activate.ps1") {
    Write-Host "[1/3] 激活虚拟环境..." -ForegroundColor Yellow
    & .\.venv\Scripts\Activate.ps1
} else {
    Write-Host "[1/3] 未找到 .venv，请先运行: python -m venv .venv" -ForegroundColor Red
    pause
    exit 1
}

# 检查 .env
if (Test-Path ".\.env") {
    Write-Host "[2/3] 发现 .env 配置" -ForegroundColor Green
    Get-Content .\.env | Where-Object { $_ -and $_ -notmatch '^\s*#' } | ForEach-Object {
        $key = ($_ -split '=', 2)[0]
        Write-Host "      ✓ $key" -ForegroundColor DarkGray
    }
} else {
    Write-Host "[2/3] 警告: 未找到 .env 文件" -ForegroundColor Red
    Write-Host "      复制 .env.example 为 .env 并填入你的 key" -ForegroundColor Yellow
    pause
    exit 1
}

# 启动服务
Write-Host ""
Write-Host "[3/3] 启动 uvicorn (http://localhost:8000)..." -ForegroundColor Yellow
Write-Host "      按 Ctrl+C 停止" -ForegroundColor DarkGray
Write-Host ""

python -m uvicorn app:app --reload --port 8000
