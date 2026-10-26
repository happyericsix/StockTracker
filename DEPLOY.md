# 服务器部署手册

> 目标读者：第一次把本项目部署到云服务器的自己。
> 前提：本机已经能 `start-all.ps1` 全栈跑通（2026-10-04 全量测试通过）。

---

## 0. 买什么

| 项 | 推荐 | 理由 |
|---|---|---|
| 机型 | 轻量应用服务器 **2核 4G** | Java(~700MB) + Python(~1.5GB, chromadb/mlflow) + MySQL(~500MB) + nginx/redis(~100MB)；2G 会紧 |
| 系统盘 | **40GB** | 五个镜像共 ~5.1GB + MySQL 数据 + 模型文件 |
| 系统 | Ubuntu 22.04 / 24.04 | 装 Docker 最顺 |
| 地域 | 国内节点（临近自己） | akshare/东财/腾讯行情访问快；用 IP 直接访问不需要备案——但要上 HTTPS/PWA（§9）就必须绑域名备案 |
| 带宽 | 按量 / ≥3Mbps 固定 | 首次拉镜像和传镜像走内网/本地 load，不需要大带宽 |

参考价：国内大厂轻量 2C4G 常年有促销（国庆期间通常有活动），月付几十元档。

## 1. 服务器初始化（一次性）

```bash
# SSH 上去后
curl -fsSL https://get.docker.com | bash          # 装 Docker（含 compose 插件）

# 2G 内存机器必做：加 2G swap（整套栈空闲态约 2.1G，超卖的部分靠 swap 兜住）
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

sudo ufw allow 22/tcp && sudo ufw allow 80/tcp    # 只开 SSH 和 HTTP
sudo ufw enable
```

时区不用担心：三个容器里 Java(-Duser.timezone)、Python(TZ=env)、MySQL(JDBC serverTimezone) 都各自钉死了东八区。

## 2. 本机构建镜像（在 Windows 开发机上）

> ⚠️ 2026-10-04 之前 build 过的镜像是**旧的**（不含当天的修复：策略详情页、复盘工具、
> 策略哨兵、axios 超时、compose 修正）。部署前必须重建。

```powershell
cd stock-tracker
docker compose build                # 首次约 10-20 分钟（Maven/npm）
docker compose config --quiet       # 校验通过即无输出

# 打包（约 5GB，redis/mysql 直接 pull 的镜像一并带上）
docker save -o st-images.tar `
  stock-tracker-app:latest stock-tracker-python-data:latest stock-tracker-frontend:latest `
  mysql:8.0 redis:7-alpine
```

## 3. 传输与服务器目录

```bash
# 本机 → 服务器（scp 或宝塔/rsync 均可，按自己习惯）
scp st-images.tar root@服务器IP:/root/
scp -r stock-tracker root@服务器IP:/opt/   # 只需要这些文件：
#   docker-compose.yml  nginx.conf  Dockerfile  .env.example
#   frontend/Dockerfile  python-data-service/Dockerfile  vendor/  secrets/
# （vendor/ 与 secrets/ 已被 .gitignore，scp 整目录最省事）

# 服务器上
docker load -i /root/st-images.tar
cd /opt/stock-tracker
cp .env.example .env
vim .env    # 改成强随机：MYSQL_ROOT_PASSWORD / INTERNAL_API_TOKEN / LLM_AES_KEY（openssl rand -hex 24）
            # DEEPSEEK_API_KEY 可选——2026-10-25 起用户 AI 花用户自己的 Key，它只服务新闻解读等系统级任务
```

`secrets/` 里是 JWT 签名密钥（app.key/app.pub）。**要么从本机拷贝，要么在服务器重新生成**
（`openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out secrets/app.key &&
openssl pkey -in secrets/app.key -pubout -out secrets/app.pub`）——重新生成只作废
旧 token，没有别的副作用。

## 4. 启动与验收

```bash
docker compose up -d --no-build     # 用 load 进来的镜像，不在服务器上编译
docker compose ps                   # 五个容器都应为 healthy/Up
```

**2G 内存机器（¥68 档轻量）**：启动命令换成带覆盖文件的一条，其余步骤完全相同：

```bash
docker compose -f docker-compose.yml -f docker-compose.small.yml up -d --no-build
```

覆盖内容：Java 堆限 512M、MySQL 缓冲池 128M（见 `docker-compose.small.yml` 注释）。
代价：回测比 4G 机器慢、偶尔借 swap。单人使用够；升配后删掉覆盖文件即可。

验收清单（按顺序）：

```bash
curl -I http://localhost/                     # 200，前端页
curl http://localhost/api/v1/market/status    # 401（未登录，说明 API 通了）
```

然后浏览器开 `http://服务器IP/`：注册新账号 → 登录 → **个人中心 → AI 模型 Key 绑定你自己的
DeepSeek Key（点「测试连通性」应通过）** → 自选股加 sh600519 → 行情能出 → 智能助手说一句话
（回复应是真分析不是模板；没绑 Key 时会有横幅提示）→ 让助手建一条策略并开模拟盘。

> 2026-10-25 起（BYOK）：用户触发的 AI（助手/委员会/盯盘/反思/记忆巩固）花用户自己在
> 个人中心绑定的 Key；未绑定的用户降级为模板回复。存量用户升级后第一次进助手页会看到
> 引导横幅——包括你自己，先去个人中心把 Key 配上。

全部通过后，docker logs stock-tracker 里应能看到定时任务的日志（价格刷新每 5 分钟一条）。

## 5. 安全清单（上公网前必做）

- [x] MySQL/Redis/Python 端口不对外（compose 已保证，只有 nginx 80 发布）
- [x] 内部接口令牌无公开默认值：`.env` 未配置时 compose 拒绝启动（2026-10-05 安全检查修复）
- [x] nginx 限流：`/api/v1/auth/` 1r/s（防爆破）、其余 API 10r/s，超限 429
- [ ] `.env` 各项全部改掉示例值（MYSQL_ROOT_PASSWORD / INTERNAL_API_TOKEN / THS_AES_KEY / LLM_AES_KEY 用 `openssl rand` 生成）
- [ ] **注册开关**：先 `AUTH_REGISTRATION_ENABLED=true` 部署 → 注册自己的账号 → 改 `false` →
      `docker compose up -d` 重建。（BYOK 后用户 AI 花自己的 Key；这条约束新闻解读等
      系统级额度与注册滥用的资源面）
- [ ] 防火墙只开 22/80（§1 的 ufw）
- [ ] SSH 改密钥登录、关密码登录（云厂商控制台可配）

> 已知未修（记录在 2026-10-05 安全检查报告）：axios/nanoid/postcss 的 3 个 high 公告
> 无可用补丁版本（axios 1.18.1 已是最新），且全部是 Node/构建链路径、不进浏览器
> bundle——不构成部署阻塞，axios 发补丁后顺手升级。

## 6. 已知的刻意取舍

- **服务器是全新数据库**：本地测试账号/策略/记忆不会迁过去。这是预期的——
  在服务器上重新注册、让助手重建策略即可。
- **python-data 没挂数据卷**：训练好的模型和向量索引在容器重建时会丢
  （`.dockerignore` 排除了它们）。测试期可接受；哪天要保模型，给 python-data
  加 named volume 挂 `/srv/python-data-service/models` 等目录。
- **HTTP only**：PWA 安装和 Web Push 推送需要 HTTPS + 域名，属于后续步骤
  （路线图里的 PWA 化）。先用 IP+HTTP 验证功能闭环。

## 7. 部署后第一次观察点

- 次一个工作日 15:30：`docker logs stock-tracker 2>&1 | grep -E "settle|Health|简报"`
  应有结算与报告日志
- 10-08（周四）开盘后：净值曲线开始记账；消息中心"复盘"tab 出现每日简报
- 周五 16:30：样本外验证运行

## 8. 手工操作数据库的字符集纪律（吃过三次亏）

往 MySQL 写中文（INSERT/UPDATE 带中文的 SQL 文件）**必须**用
`--default-character-set=utf8mb4` 的客户端，否则中文会以双重编码形态入库
（显示成 `ä¸­æ–‡`）。`docker exec` 是非交互终端，mysql 客户端默认 latin1，
这是三次事故的共同根因（10-07 演示新闻、10-08 direction 字段、10-09 授权备注）。

```bash
# $PW 现场从容器环境变量取，不落盘（别写进脚本/文件，注意别回显进 Shell 历史）：
PW=$(docker exec stock-mysql printenv MYSQL_ROOT_PASSWORD)
# 正确姿势
docker exec -i stock-mysql mysql -uroot -p$PW --default-character-set=utf8mb4 stockdb < fix.sql
```

已在容器内写入 `/root/.my.cnf`（`[client] default-character-set=utf8mb4`）兜底，
不带参数的 `docker exec stock-mysql mysql ...` 也安全。注意该文件在容器文件系统里，
**容器重建（compose up --force-recreate / 换镜像）会丢**，重建后需重写。

修复双重编码数据：先把该列 HEX 拉下来本地判定（cp1252 段能否解回有效 UTF-8），
混合文本（模板正确 + 内嵌乱码）不能整列 latin1 往返，要分段修复；纯乱码列可以直接
`CONVERT(CAST(CONVERT(col USING latin1) AS BINARY) USING utf8mb4)`。

---

## 9. 上 HTTPS（PWA 安装提示的最后一块拼图）

PWA 的 manifest / Service Worker / 离线策略前端早已配齐（vite-plugin-pwa），
卡住的只有一件事：**浏览器判定"可安装"要求安全上下文（HTTPS 或 localhost）**。
裸 HTTP 下 SW 无法注册、安装横幅永远不出现。所以这条路 = 域名 → 备案 → 证书 →
换 nginx 配置，四步走完 PWA 立刻全量生效。仓库侧已备好：`nginx-https.conf.example`
（成品配置模板）、compose 已预留 443 端口与证书挂载（不换配置前无副作用）。

### 9.1 域名与备案（约 7~15 天，流程中最慢的一段）

1. 买域名并完成**域名实名认证**（1~3 天，不过实名不能提交备案）。
2. 在云厂商备案系统提交个人备案（服务器在哪家电在哪家办）：全程线上、免费，
   需大陆节点服务器剩余时长 ≥ 3 个月。**网站名称/内容描述避开金融字眼**
   （股票/量化/投资都别写），用"个人学习记录/技术实践"这类中性表述——
   个人备案对金融类目驳回很严。
3. 工信部短信核验收到后 **24 小时内**完成，错过重报。
4. 备案通过拿到备案号。**合规要求：网站底部挂备案号并链接到
   beian.miit.gov.cn**（前端 footer 加一行，说一声就加）。

### 9.2 解析与证书（备案下来当天可完成）

```bash
# ① DNS：A 记录 → 120.27.226.47（备案通过前不要解析到大陆服务器对外服务）

# ② 服务器上装 acme.sh（Let's Encrypt 免费证书，DNS-01 校验：
#    不依赖 80 端口、不用动 nginx，先签证书后改配置，没有鸡生蛋问题）
curl https://get.acme.sh | sh -s email=你的邮箱@example.com

# ③ 阿里云 RAM 建一个只有 DNS 解析权限的子账号，AK 填进环境变量
#    （只给 AliyunDNSFullAccess；泄露的最坏后果是别人改你的解析）
export Ali_Key="..."
export Ali_Secret="..."

# ④ 签发并安装到 compose 挂载的目录（容器内路径 /etc/nginx/certs 只读）
acme.sh --issue --dns dns_ali -d stock.example.com
acme.sh --install-cert -d stock.example.com \
  --fullchain-file /opt/stock-tracker/secrets/certs/fullchain.pem \
  --key-file       /opt/stock-tracker/secrets/certs/privkey.pem \
  --reloadcmd "docker exec stock-tracker-nginx nginx -s reload"
# 续期全自动：acme.sh 的 cron 到期前重签，续上后 reloadcmd 重载 nginx，
# 不用重启容器、不用重新部署。域名不在阿里云解析就换 --dns 对应的 provider。
```

### 9.3 切换配置与验收

```bash
# ⑤ 换配置：模板里两处 stock.example.com 改成真实域名
cp nginx-https.conf.example nginx.conf   # 先改域名再覆盖

# ⑥ 部署前预检（本机 Docker 跑一次 nginx -t，配置错误别带到服务器上才发现；
#    自签证书只为让 nginx 能解析配置，与真实证书无关）
openssl req -x509 -newkey rsa:2048 -keyout /tmp/privkey.pem -out /tmp/fullchain.pem \
  -days 1 -nodes -subj "//CN=test"
docker run --rm --add-host app:127.0.0.1 \
  -v "$PWD/nginx.conf:/etc/nginx/conf.d/default.conf:ro" \
  -v /tmp:/etc/nginx/certs:ro nginx:alpine nginx -t
#   （Git Bash 里 subj 要写 //CN=test 防 MSYS 路径改写；PowerShell 用 /CN=test）

# ⑦ 预检通过后走 §2~§4 的既有部署流（build → save → scp → load → up -d）
```

验收清单（按序勾）：

- [ ] `https://域名` 打开正常，地址栏有锁
- [ ] DevTools → Application → Service Workers 显示 activated（HTTP 下这行永远起不来）
- [ ] Chrome/Edge 地址栏右侧出现"安装"图标；安卓 Chrome 弹安装横幅
- [ ] 断网刷新：应用壳还在（workbox 离线兜底），资讯按缓存策略降级而非白屏
- [ ] `http://域名` 301 到 https；`http://IP` 仍可直连（IP 入口刻意保留，无强制跳转）
- [ ] iOS Safari：分享 → 添加到主屏幕（iOS 不弹系统横幅，apple-touch-icon 已备好）

回退：`git checkout nginx.conf` 重打 frontend 镜像重新部署即可；证书文件留在
`secrets/certs/` 不碍事。HSTS 在模板里刻意注释着——HTTPS 稳定跑几周前不要开，
它一旦下发，浏览器的强制 https 会把回退窗口直接关死。

---

部署遇到问题先看 `docker logs`（stock-tracker / stock-python-data / stock-mysql），
90% 的首部署问题（数据库连不上、token 没配、key 缺失）都会在日志里直说。
