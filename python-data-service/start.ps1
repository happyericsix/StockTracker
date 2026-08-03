# start.ps1 - 一键启动 Python 数据服务
# 用法: .\start.ps1

Set-Location -Path $PSScriptRoot

Write-Host "========================================" -ForegroundColor Cyan
Write-Host " Stock Tracker Python Data Service" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""

# 1. activate venv
Write-Host "[1/3] activate venv..." -ForegroundColor Yellow
& ".\.venv\Scripts\Activate.ps1"

# 2. check .env
Write-Host "[2/3] check .env..." -ForegroundColor Yellow
if (Test-Path ".\.env") {
    Write-Host "      .env OK" -ForegroundColor Green
} else {
    Write-Host "      WARNING: .env not found" -ForegroundColor Red
}

# 3. start uvicorn
Write-Host ""
Write-Host "[3/3] start uvicorn on http://localhost:8000" -ForegroundColor Yellow
Write-Host "      Ctrl+C to stop" -ForegroundColor DarkGray
Write-Host ""

python -m uvicorn app:app --reload --port 8000
