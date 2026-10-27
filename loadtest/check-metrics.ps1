# 压测期间的应用健康面板：每 5 秒抓一次 actuator 关键指标 + Redis 容器资源，
# 追加写入 CSV 并打印到控制台。压测跑起来后在**另一个终端**开着它。
#
# 前提：Java 以如下环境变量启动（见 loadtest\README.md 第 1 步）：
#   $env:MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE = 'health,info,metrics'
# 本项目的 Spring Security 把 /actuator/** 也保护起来了（anyRequest().authenticated()），
# 所以脚本会先用测试账号登录拿 JWT 再访问指标。
#
# 用法：powershell -ExecutionPolicy Bypass -File .\check-metrics.ps1
#       .\check-metrics.ps1 -BaseUrl http://localhost:8080 -Username loadtest_0
param(
    [string]$BaseUrl = 'http://localhost:8080',
    [int]$IntervalSec = 5,
    [string]$OutCsv = "$PSScriptRoot\metrics.csv",
    [string]$Username = 'loadtest_0',
    [string]$Password = 'LoadTest#2026'
)

# 指标清单——各自回答一个问题：
#   hikaricp.connections.pending  >0 持续 = MySQL 连接池（默认 10）成瓶颈
#   hikaricp.connections.active   贴近 10 = 池子快满了
#   jvm.threads.live              线程总数，长跑持续增长 = 线程泄漏
#     （要真正的 tomcat.threads.busy 需 SERVER_TOMCAT_MBEANREGISTRY_ENABLED=true，
#       Boot 3+ 默认不注册该 MBean——restart-java.ps1 已带上）
#   jvm.memory.used               MB，看走势：锯齿回落正常，只爬不落=泄漏
#   process.cpu.usage             0~1，持续 >0.9 = CPU 成瓶颈
$metrics = @(
    'hikaricp.connections.active',
    'hikaricp.connections.pending',
    'jvm.threads.live',
    'jvm.memory.used',
    'process.cpu.usage'
)

# ---- 登录换 JWT（actuator 也走鉴权）----
$auth = $null
try {
    $auth = Invoke-RestMethod -Method Post -Uri "$BaseUrl/api/v1/auth/login" `
        -ContentType 'application/json' -TimeoutSec 5 `
        -Body (@{ username = $Username; password = $Password } | ConvertTo-Json)
} catch {}
if (-not $auth -or -not $auth.token) {
    Write-Warning "测试账号 $Username 登录失败（先跑一次 k6 baseline.js 创建账号），actuator 指标将取不到"
}
$hdr = @{ Authorization = "Bearer $($auth.token)" }

$header = (@('timestamp', 'health') + ($metrics | ForEach-Object { $_ -replace '\.', '_' })) -join ','
if (-not (Test-Path $OutCsv)) {
    Set-Content -Path $OutCsv -Value $header
}
Write-Host "监控 $BaseUrl 每 ${IntervalSec}s 一采，写入 $OutCsv（Ctrl+C 停止）"

while ($true) {
    $row = @((Get-Date -Format 'HH:mm:ss'))
    try {
        $row += (Invoke-RestMethod "$BaseUrl/actuator/health" -Headers $hdr -TimeoutSec 3).status
    } catch {
        $row += 'ERR'   # 应用无响应本身就是重要信号，记下来而不是中断
    }
    foreach ($m in $metrics) {
        try {
            $v = (Invoke-RestMethod "$BaseUrl/actuator/metrics/$m" -Headers $hdr -TimeoutSec 3).measurements[0].value
            if ($m -eq 'jvm.memory.used') { $v = [math]::Round($v / 1MB) }
            $row += [math]::Round([double]$v, 3)
        } catch {
            $row += ''
        }
    }
    $line = $row -join ','
    Add-Content -Path $OutCsv -Value $line
    Write-Host $line
    try {
        docker stats --no-stream --format "  redis: {{.MemUsage}}  CPU {{.CPUPerc}}" stock-redis 2>$null
    } catch {}
    Start-Sleep -Seconds $IntervalSec
}
