# 本地高并发测试方案

针对本地全栈（Java :8080 + Python :8000 + MySQL :3306 + Redis :6379）的压测套件。
目标：在**不打真实上游**（akshare/THS/DeepSeek）的前提下，回答四个问题——

1. 各接口的正常延迟是多少？（基线）
2. 系统到多少并发开始撑不住？瓶颈在哪？（容量拐点）
3. 开盘瞬间流量冲进来扛不扛得住、多久恢复？（冲击）
4. 长时间运行会不会漏内存/漏连接？（浸泡 + SSE 长连接）

**不测**：AI 聊天路径（`/api/v1/chat` 等）——烧 DeepSeek 额度，且它该测的是
限流与预算闸，不是吞吐。写接口（加自选/下单）只低频冒烟，不进高压场景。

## 目录

| 文件 | 作用 |
|---|---|
| `stub/record_replay_proxy.py` | 录制-回放代理：每个上游 URL 只放行第一次真实请求，之后全部本地回放 |
| `k6/baseline.js` | 场景一：基线（5 并发 × 3 分钟），同时完成 stub 缓存录制 |
| `k6/capacity.js` | 场景二：容量阶梯（逐级 50→100→200→400 并发找拐点） |
| `k6/spike.js` | 场景三：开盘冲击（20 秒冲到 300 并发再撤退，观察恢复） |
| `k6/soak.js` | 场景四：浸泡（恒定并发长跑，测泄漏） |
| `sse_hold.py` | SSE 长连接保持（k6 不支持 SSE，单独用 Python 挂连接） |
| `check-metrics.ps1` | 压测期间的应用指标面板（Hikari/线程/JVM/Redis，写 CSV） |
| `restart-java.ps1` | 只重启 Java 并注入压测环境变量（stub 地址/actuator 暴露等） |
| `bin/k6-v2.3.0-windows-amd64/k6.exe` | k6 便携版已随套件提供，无需另装 |
| `results/report-2026-10-27.md` | 首轮完整压测报告（含 SSE 连接上限发现） |

> 注：`.ps1` 文件是 UTF-8 **带 BOM** 编码（PowerShell 5.1 读无 BOM 的中文注释会
> 乱码进而破坏语法），编辑时保持该编码。

## 第 0 步：一次性准备

k6 已随套件内置在 `loadtest\bin\k6-v2.3.0-windows-amd64\k6.exe`，无需安装；
想全局可用可另装：`winget install -e --id Grafana.Labs.k6`。
下文命令统一写 `k6`，实际执行请用完整路径或先 `set PATH=%PATH%;loadtest\bin\k6-v2.3.0-windows-amd64`。

## 第 1 步：每轮压测前——启动隔离环境

**终端 A**：先起录制-回放代理（它默认把上游指向 :8000 的真实 python-data）：

```powershell
cd stock-tracker
python loadtest\stub\record_replay_proxy.py
# 看到 "record-replay proxy :8001 -> upstream http://localhost:8000" 即就绪
```

**终端 B**：让 Java 带压测专用环境变量启动。**推荐直接用 `restart-java.ps1`**
（它自己注入变量，避免在 bash/PowerShell 之间传环境变量的引号坑——首轮执行
就栽在这里：`$env:` 被 bash 展开成空，Java 静默直连了真实上游）：

```powershell
cd stock-tracker
powershell -ExecutionPolicy Bypass -File loadtest\restart-java.ps1
```

若整栈都没起，先 `.\start-all.ps1` 起一遍（不带变量也行），再用上面的脚本重启
Java。注意：在 bash 里给 powershell 传 `$env:...` 内联赋值会被 bash 吞掉引号，
务必走脚本文件。

> ⚠️ 忘设 `AKSHARE_API_BASE_URL` 就开压 = 压测流量全部透传到真实 akshare，
> 可能导致 IP 被封。判断方法：终端 A 的 stub 控制台全程没有 `[recorded]`
> 输出却一切"正常"，说明 Java 根本没走 stub——立即停止重配。

验证：`curl http://localhost:8080/actuator/health` 返回 `{"status":"UP"...}`。
（不要用 curl 直接戳 stub 预热——不带内部 token 会把 401 录进缓存。预热交给基线脚本。）

**终端 C**（可选但建议）：起指标面板，整个压测期间开着：

```powershell
cd stock-tracker\loadtest
powershell -ExecutionPolicy Bypass -File .\check-metrics.ps1
```

## 第 2 步：按顺序执行场景

所有 k6 命令在 `stock-tracker` 目录下执行；结果同时存档：

```powershell
New-Item -ItemType Directory -Force results | Out-Null
```

### 2.1 基线（必须第一个跑——它顺带完成 stub 录制）

```powershell
k6 run loadtest\k6\baseline.js | Tee-Object results\baseline.txt
```

**看什么**：summary 里每个接口（favorites_brief / stock_detail / search /
market_status）的 avg / p(95) / p(99)。把这些数抄进第 4 节的记录表，
它们是后面所有场景的对照基准。

### 2.2 容量阶梯（逐级找拐点）

```powershell
k6 run loadtest\k6\capacity.js -e VUS=50  -e DURATION=3m | Tee-Object results\cap-50.txt
k6 run loadtest\k6\capacity.js -e VUS=100 -e DURATION=3m | Tee-Object results\cap-100.txt
k6 run loadtest\k6\capacity.js -e VUS=200 -e DURATION=3m | Tee-Object results\cap-200.txt
k6 run loadtest\k6\capacity.js -e VUS=400 -e DURATION=3m | Tee-Object results\cap-400.txt
```

**拐点定义**：错误率 >1%，或 P99 超过基线 3 倍的那一级。它的**前一级**就是当前容量。
每级跑的时候盯终端 C：先见红的指标就是瓶颈排序（见第 3 节解读表）。

### 2.3 开盘冲击

```powershell
k6 run loadtest\k6\spike.js | Tee-Object results\spike.txt        # 峰值 300
k6 run loadtest\k6\spike.js -e PEAK=500 | Tee-Object results\spike-500.txt
```

**看什么**：峰值期错误率（目标 <2%）；更关键的是最后 60 秒"恢复观察段"——
延迟应回到基线水平，回不去说明有请求堆积/连接泄漏。

### 2.4 浸泡（放最后，耗时最长）

```powershell
k6 run loadtest\k6\soak.js | Tee-Object results\soak-30m.txt                        # 快速版
k6 run loadtest\k6\soak.js -e VUS=100 -e DURATION=2h | Tee-Object results\soak-2h.txt  # 完整版
```

**看什么**：终端 C 的 `jvm_memory_used` 列——锯齿状回落 = 正常 GC；
只爬不落 = 泄漏，停测排查（常见嫌疑：SSE emitter 未关闭、Redis 连接、缓存失效策略）。

### 2.5 SSE 长连接（与上面独立，可单独跑）

```powershell
python loadtest\sse_hold.py -n 100 -t 300
python loadtest\sse_hold.py -n 200 -t 600
```

**看什么**：`alive` 稳定在目标数、`connect_fail=0`；`reconnects` 持续增长 =
服务端在主动掐连接，去 Java 日志找原因。对 1.8G 内存的生产机，
"同时挂多少条 SSE"很可能先于 QPS 到顶，这个数值得单独记下来。

> ⚠️ 2026-10-27 首轮实测发现：SSE 存在**全局 10 条并发上限**（第 11 条起挂起
> 30s），根因是 OSIV 钉住数据库连接 + Hikari 池默认 10，详见
> `results/report-2026-10-27.md` 第三节。若你已按建议关闭
> `spring.jpa.open-in-view` 或调大连接池，用本脚本重跑 `-n 100 -t 180` 验证上限
> 是否解除。判别"每用户上限"还是"全局上限"：换 `--username loadtest_1` 复测。

## 第 3 步：指标解读（check-metrics.ps1 各列）

| 指标 | 健康形态 | 异常含义 |
|---|---|---|
| `hikaricp_connections_pending` | 恒为 0 | >0 且持续 = MySQL 连接池（默认 10）成瓶颈 |
| `hikaricp_connections_active` | 低于 10 | 长期贴 10 = 池子将满，考虑调大或查慢 SQL |
| `tomcat_threads_busy` | 波动、低于 200 | 贴 200 = 线程池打满，请求在排队 |
| `jvm_memory_used` | 锯齿回落 | 单调爬升 = 内存泄漏 |
| `process_cpu_usage` | 波动 | 持续 >0.9 = CPU 瓶颈 |
| `health` | UP | ERR = 应用已无响应，看是否 OOM |

MySQL 是宿主机原生服务，另开终端用 `mysqladmin -uroot -p status` 看 Threads/慢查询。

## 第 4 步：通过标准与结果记录

| 场景 | 通过标准 |
|---|---|
| 基线 | 错误率 0，各接口 P95 < 500ms（超了先优化再压） |
| 容量 | 拐点 ≥ 100 并发时错误率仍 <1% 且 P99 < 800ms（本地机） |
| 冲击 | 峰值错误率 <2%，撤退后 60 秒内恢复基线延迟 |
| 浸泡 | 30 分钟 jvm_memory_used 锯齿回落、错误率 <1%、无重启 |
| SSE | 100 条全连上、不掉零、无服务端主动断连 |

结果记录表（复制到 results\notes.md 手填）：

```markdown
| 日期 | 场景 | 并发 | P95 | P99 | 错误率 | 瓶颈指标 | 备注 |
|------|------|------|-----|-----|--------|----------|------|
|      | baseline | 5  |     |     |        |          |      |
|      | capacity | 50 |     |     |        |          |      |
```

## 结果如何映射到生产

本地机的 CPU/内存 ≠ 生产 1.8G 小机器，**绝对并发数不能直接照搬**；能直接搬的是
**瓶颈排序**（谁先红：连接池 / 线程 / 内存 / CPU）和**错误行为**（超时还是 5xx、
恢复快慢）。要拿生产级数字，把这套 loadtest 目录原样拷到一台同规格临时云主机上，
同一套命令重跑即可（compose 部署时把 stub 的 UPSTREAM 指向 python-data 容器名）。

## FAQ

- **setup 阶段报"登录/注册均失败"**：确认本地 MySQL 在跑、`.\start-all.ps1 -Status`
  全绿；或换起始编号 `-e USER_OFFSET=100` 避开脏数据。
- **`/actuator/metrics` 404**：第 1 步的暴露变量没生效（Java 是旧进程），重启 Java。
- **个别接口 404/400**：`SYMBOLS` 里的代码格式本地不认（如需 `sh600519` 前缀风格），
  用 `-e SYMBOLS=sh600519,sz000001` 重跑基线。
- **想让 stub 重新录**：删 `loadtest\stub\.cache` 目录。
- **压测中想紧急停**：k6 终端 Ctrl+C；应用异常先 `.\start-all.ps1 -Stop` 再排查日志。
- **跑完想恢复日常开发**：不带第 1 步的环境变量重启 `.\start-all.ps1` 即可，
  stub 进程关掉，测试账号留在库里无副作用（想清理：删 `loadtest_%` 用户）。
