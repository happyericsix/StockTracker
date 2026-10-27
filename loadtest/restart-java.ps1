# 重启 Java 后端（只动 Java，Python/Redis/Vite 不碰）并注入压测专用环境变量。
# 供压测会话反复使用：比如 Java 带着旧环境变量在跑、或想换 stub 地址重开一轮。
#
# 用法：powershell -ExecutionPolicy Bypass -File .\restart-java.ps1
#       .\restart-java.ps1 -AkshareStub http://localhost:8001
param(
    [string]$AkshareStub = 'http://localhost:8001'
)

$Root = Split-Path -Parent $PSScriptRoot   # loadtest -> stock-tracker

# 先停现有 Java（过滤条件与 start-all.ps1 -Stop 保持一致，避免误杀 IDE 等其他 Java 进程）
Get-CimInstance Win32_Process -Filter "Name='java.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*stocktracker*' -or $_.CommandLine -like '*spring-boot:run*' } |
    ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
        Write-Host "已停止 java pid $($_.ProcessId)"
    }
Start-Sleep -Seconds 2
if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
    Write-Error '8080 仍被占用，请手动结束占用进程后重试'
    exit 1
}

# 与 start-all.ps1 相同：从根 .env 注入 INTERNAL_API_TOKEN 等；压测变量在这之后设置，
# 保证优先级（万一 .env 里也有同名键）
$rootEnv = Join-Path $Root '.env'
if (Test-Path $rootEnv) {
    Get-Content $rootEnv | ForEach-Object {
        if ($_ -match '^\s*([A-Z_]+)\s*=\s*(.*)\s*$' -and $_ -notmatch '^\s*#') {
            Set-Item -Path ('Env:' + $Matches[1]) -Value $Matches[2]
        }
    }
    Write-Host '已从根 .env 注入 INTERNAL_API_TOKEN 等'
}
$env:AKSHARE_API_BASE_URL = $AkshareStub
$env:MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE = 'health,info,metrics'
$env:SPRING_JPA_SHOW_SQL = 'false'
# Boot 3+ 默认不注册 Tomcat MBean → actuator 里没有 tomcat.threads.busy；
# 压测时打开它才能看到线程池水位
$env:SERVER_TOMCAT_MBEANREGISTRY_ENABLED = 'true'
Write-Host "AKSHARE_API_BASE_URL = $env:AKSHARE_API_BASE_URL"

Start-Process -FilePath (Join-Path $Root 'mvnw.cmd') -ArgumentList 'spring-boot:run' `
    -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $Root 'logs\java.out.log') `
    -RedirectStandardError  (Join-Path $Root 'logs\java.err.log')

for ($i = 1; $i -le 120; $i++) {
    Start-Sleep -Seconds 2
    if (Get-NetTCPConnection -LocalPort 8080 -State Listen -ErrorAction SilentlyContinue) {
        Write-Host "Java 就绪（约 $($i * 2)s）"
        exit 0
    }
}
Write-Error 'Java 240s 内未就绪，查看 stock-tracker\logs\java.err.log'
exit 1
