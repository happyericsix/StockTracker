# deploy-update.ps1 —— 日常更新某個服务的镜像并推上服务器（一条命令代替 build/save/scp/load/up 五步）
#
# 用法：
#   .\deploy-update.ps1 python-data     # 只更新 Python 服务（改了 python-data-service/ 或 vendor/）
#   .\deploy-update.ps1 app             # 只更新 Java 后端（改了 src/ 或 pom.xml）
#   .\deploy-update.ps1 frontend        # 只更新前端（改了 frontend/ 或 nginx.conf）
#   .\deploy-update.ps1 -ServerIp x.x.x.x app   # 换服务器时指定 IP
#
# 前提：服务器已按 DEPLOY.md 部署过（/opt/stock-tracker 就位）。
# 数据安全：镜像更新不碰 mysql-data 卷，服务器上的数据（账号/策略/模拟盘）不受影响。
# 配置类改动（.env）不需要本脚本：直接 ssh 上去改 .env 后 up -d 重建容器即可。
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [ValidateSet("app", "python-data", "frontend")]
    [string]$Service,
    [string]$ServerIp = "120.27.226.47"
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Tar = Join-Path $Root "deploy-out\st-update-$Service.tar"

$image = switch ($Service) {
    "app"          { "stock-tracker-app:latest" }
    "python-data"  { "stock-tracker-python-data:latest" }
    "frontend"     { "stock-tracker-frontend:latest" }
}

Write-Host "[1/5] 构建 $Service ..." -ForegroundColor Cyan
Push-Location $Root
docker compose build $Service
if ($LASTEXITCODE -ne 0) { throw "构建失败" }
Pop-Location

Write-Host "[2/5] 导出镜像 ..." -ForegroundColor Cyan
docker save -o $tar $image
if ($LASTEXITCODE -ne 0) { throw "导出失败" }
$size = "{0:N0} MB" -f ((Get-Item $tar).Length / 1MB)
Write-Host "      $size"

Write-Host "[3/5] 上传到 $ServerIp（输一次密码）..." -ForegroundColor Cyan
scp $tar "root@${ServerIp}:/root/"
if ($LASTEXITCODE -ne 0) { throw "上传失败" }

Write-Host "[4/5] 服务器加载并重建容器 ..." -ForegroundColor Cyan
ssh root@$ServerIp "docker load -i /root/st-update-$Service.tar && cd /opt/stock-tracker && docker compose -f docker-compose.yml -f docker-compose.small.yml up -d --no-build $Service"
if ($LASTEXITCODE -ne 0) { throw "服务器更新失败" }

Write-Host "[5/5] 容器状态 ..." -ForegroundColor Cyan
ssh root@$ServerIp "cd /opt/stock-tracker && docker compose ps $Service"

Write-Host "`n完成：$Service 已更新到 $ServerIp" -ForegroundColor Green
