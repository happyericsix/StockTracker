# Stock Tracker

> **AI 驱动的个人股票助手** —— 不只是查行情，更是看懂行情。

[![Java](https://img.shields.io/badge/Java-21-orange)]()
[![Spring Boot](https://img.shields.io/badge/Spring%20Boot-4-green)]()
[![Vue](https://img.shields.io/badge/Vue-3-brightgreen)]()
[![Python](https://img.shields.io/badge/Python-3.11+-blue)]()
[![License](https://img.shields.io/badge/License-MIT-purple)]()

> ⚠️ **免责声明**：本项目仅供学习与技术研究，不构成任何投资建议。市场有风险，决策需谨慎。

---

## 这是什么 / 这不是什么

市面上量化工具（聚宽、米筐、BigQuant）解决的是"专业 quant 怎么用代码挖 alpha 因子"——这**不是本项目的方向**。

本项目的目标用户是**普通散户**：
- 想知道"**这只股票现在该不该买/卖**"，但看不懂研报
- 想在自己手机里**随手查行情、收到价格预警**
- 想要 AI 帮他**把技术指标和模型预测翻译成人话**

**核心差异化**：**多模型 ML 预测 + 自然语言解释 + PWA 移动端**。

| 能力 | 传统量化平台 | 传统股票 App | **Stock Tracker** |
|---|---|---|---|
| 实时行情 | ✅ | ✅ | ✅ |
| 因子挖掘 / 选股组合 | ✅ | ❌ | ❌ |
| 单股 ML 预测 | ❌ | ❌ | ✅（LGB/Transformer/DQN，试验性、默认下线） |
| 回测验证策略 | ✅ | ❌ | ✅ |
| 自然语言交互 | ❌ | ❌ | ✅（DeepSeek） |
| 移动端 / 推送 | ❌ | ✅ | ✅（PWA，可加主屏 + 推送） |
| 可解释性 | 看代码 | ❌ | ✅（AI 翻译指标） |

---

## 核心特性

### 🧠 AI 模型预测（核心亮点）

> ⚠️ **实现状态（与代码对齐）**：本仓库默认只把**规则技术信号**用于用户可见结论。
> LightGBM 三窗口预测与 Transformer/DQN 是**离线/按需训练管线**（需手动运行
> `python-data-service/train_models.py`），基于"未通过前向验证不外放"的克制原则，
> **默认不参与在线预测输出**（详见「AI 模型说明」）。

- **LightGBM 三窗口预测**：60d / 120d / 全量历史三套差异化参数（离线脚本 / 按需加载）
- **MiniTransformer / DQN**：训练管线已完成但**默认下线**，前向验证通过前不对外输出
- **技术信号打分**：RSI / MACD / 均线 / 布林带 综合评分（在线默认路径）
- **回测验证**：夏普比率、最大回撤、胜率、盈亏比，**让策略自己交账**

模型与回测结果落盘（`models/*.pkl` / MLflow），重启不丢；
**当前没有后台定时重训**——重训由 `train_models.py` 手动触发，或在请求路径按需加载。

### 📊 可解释性

不只是"看涨/看跌"——告诉你**为什么**：
- 各因子的贡献度（feature importance）
- 当前 RSI / MACD / 均线状态
- 回测胜率和历史表现
- LLM 用大白话综合讲解

### 📱 PWA 移动端（路线图）

下一步会把前端升级为 PWA：
- 扫码即用，**添加到主屏像原生 App**
- **价格预警推送**（Web Push API，无需 App Store）
- 离线缓存常用股票数据
- 自选股、行情、模型分析一站式

### 🔐 完整工程能力

- JWT (RSA 签名) 鉴权
- Caffeine + Redis 二级缓存（5ms 命中）
- Docker Compose 一键部署
- MLflow 实验追踪
- RAG（Chroma 向量库）—— 接入研报 / 新闻情感

### 🧠 Strategy Agent（自然语言 → 策略 JSON）

用户可以在聊天框用自然语言描述交易策略，Agent 会自动查行情、构建并校验策略 JSON，再把同一份配置落库到「策略库」。

- **流程**：前端聊天 → Java `ChatService` → Python `/api/v1/agent/chat` → ReAct Agent → 返回 `{replies, strategy_json}` → Java 自动保存策略并推送回复
- **策略 JSON 是唯一契约**：回测、模拟盘、未来实盘引擎都读取同一份结构化配置
- **v1 规则**：MA 金叉/死叉、RSI 超买/超卖、MACD 金叉/死叉、价格上穿/下穿、止盈/止损/跟踪止盈，`all`/`any` 条件组合，全仓/百分比仓位
- **策略库**：`/strategies` 列表，`/strategies/:id` 详情，支持回测、模拟盘启停、虚拟账户与成交记录展示
- **模拟盘**：Java `PaperTradingService` 日频结算，策略级隔离，`strategyId + tradeDate` 幂等去重

> ⚠️ 模拟盘是**虚拟账户**，仅用于验证策略逻辑，不代表真实收益，也不构成投资建议。

---

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端框架 | Spring Boot 4 + Java 21 |
| 数据服务 | Python 3.11+ / FastAPI |
| 前端 | Vue 3 + Vite（PWA：manifest/SW 已配置，Web Push 与离线体验待完善） |
| 数据库 | MySQL 8（生产） / H2（开发） |
| 缓存 | Caffeine (本地) + Redis (分布式) |
| 机器学习 | LightGBM + 手写 Transformer + DQN + FinRL |
| 实验追踪 | MLflow |
| LLM | DeepSeek（自然语言解释） |
| 部署 | Docker Compose |

---

## 架构图

```
┌─────────────────────────────────────────────────────────────┐
│                  用户端（Web / PWA）                          │
│           Vue 3 + Vite → 升级为 PWA（可安装到桌面）           │
└────────────────────────┬────────────────────────────────────┘
                         │  HTTPS / JWT
                         ▼
┌─────────────────────────────────────────────────────────────┐
│         Spring Boot 4（端口 8080）                            │
│   鉴权 / 自选股 / 缓存 / 业务编排                              │
└────────┬──────────────────────────────────┬─────────────────┘
         │                                  │
         ▼                                  ▼
┌──────────────────────┐         ┌──────────────────────────┐
│  Python 数据服务      │         │   MySQL + Redis          │
│  FastAPI (8000)      │         │   数据 / 缓存 / 会话      │
│                      │         └──────────────────────────┘
│  • akshare 行情       │
│  • 技术指标           │
│  • LightGBM (3窗口)   │
│  • Transformer       │
│  • DQN 强化学习       │
│  • 回测引擎           │
│  • LLM 解释           │
│  • MLflow 追踪        │
└──────────────────────┘
```

---

## 快速开始

### 前置条件

- JDK 21
- Docker（运行 MySQL + Redis）
- Node.js 18+
- Python 3.11+

### 1. 启动基础服务

```bash
docker run -d --name stock-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=123456 -e MYSQL_DATABASE=stockdb mysql:8
docker run -d --name stock-redis -p 6379:6379 redis:7-alpine
```

### 2. 启动后端（Spring Boot）

```bash
# IDEA：直接运行 StocktrackerApplication
# 或命令行：
$env:INTERNAL_API_TOKEN="your-secret-token"
.\mvnw.cmd spring-boot:run
# → http://localhost:8080
```

### 3. 启动 Python 数据服务

```bash
cd python-data-service
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
# 配置 LLM（可选，不配也能用模板回复）
copy .env.example .env
# 编辑 .env 填入 DEEPSEEK_API_KEY
.\start.ps1
# → http://localhost:8000
```

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

### Docker Compose 一键启动

```bash
docker compose up -d
# Redis    → localhost:6379
# 后端     → localhost:8080
# 前端     → http://localhost
```

---

## 股票代码格式

| 市场 | 格式 | 示例 |
|---|---|---|
| 沪市 A 股 | `sh` + 代码 | `sh600519`（贵州茅台） |
| 深市 A 股 | `sz` + 代码 | `sz300750`（宁德时代） |
| 北交所 | `bj` + 代码 | `bj920xxx` |
| 港股 | `hk` + 代码 | `hk00700`（腾讯控股） |
| 美股 | 直接代码 | `AAPL` / `MSFT` / `TSLA` |
| 自动识别 | 6 开头→沪市 | `600519` → `sh600519` |

---

## API 接口

所有接口（除认证外）都需要 `Authorization: Bearer <token>`。

### 认证

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/auth/register` | 注册 |
| POST | `/api/v1/auth/login` | 登录（返回 JWT） |

### 股票查询

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/stocks/{symbol}` | 实时行情 |
| GET | `/api/v1/stocks/{symbol}/overview` | 公司概况 |
| GET | `/api/v1/stocks/{symbol}/history?page=0&size=30` | K 线历史 |

### 自选股

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/stocks/favorites` | 自选股 + 实时价 |
| POST | `/api/v1/stocks/favorites` | 添加自选 |
| DELETE | `/api/v1/stocks/favorites/{symbol}` | 删除自选 |

### AI 分析（Python 服务）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/indicators/{symbol}` | 技术指标 + LGB 预测 |
| GET | `/api/v1/backtest/{symbol}?capital=100000` | 回测报告 |
| GET | `/api/v1/stocks/search?keyword=...` | 股票搜索（联想） |

**示例**：

```bash
# 查贵州茅台
curl -H "Authorization: Bearer <token>" \
  http://localhost:8080/api/v1/stocks/sh600519

# 拿 AI 分析（含预测、信号、特征重要性）
curl http://localhost:8000/api/v1/indicators/sh600519

# 跑回测
curl "http://localhost:8000/api/v1/backtest/sh600519?capital=100000"
```

---

## AI 模型说明

### 训练流程

1. 拉取该股历史 K 线（akshare）
2. 计算技术指标 + 构造特征（20+ 维）
3. **三窗口分别训练** LGB（60d / 120d / 全量）
4. Transformer 训练时序表示
5. DQN 训练交易策略
6. 结果落盘 `models/{symbol}_*.pkl`
7. 6 小时后自动重训（覆盖每日收盘 15:00）

### 防泄漏

- **时序切分**：训练集是历史 80%，验证集是最近 20%（不是随机划分）
- **三窗口差异化调参**：短窗口用快指标（MA3/MA10），长窗口用慢指标（MA20/MA60）
- **Prompt 审慎化**：默认"不预测"语气，给出"近期偏多/偏空"而不是"明天必涨"

### 当前局限（透明告知）

- 在线默认只输出规则技术信号；LGB/Transformer/DQN 预测未接入在线路径（需先做 walk-forward 前向验证）
- LGB 对**长期趋势**预测能力有限（R² 通常 0.1~0.4）
- Transformer 训练数据量小（numpy 手写，无 GPU 加速）
- DQN 策略可能过拟合历史行情
- **回测 ≠ 实盘**：未考虑滑点、流动性、停牌、T+1、整手与印花税

---

## 项目结构

```
stock-tracker/
├── frontend/                    # Vue 3 前端（将升级为 PWA）
│   ├── src/
│   │   ├── api/                 # API 调用层
│   │   ├── components/          # 通用组件
│   │   ├── pages/               # 页面
│   │   │   ├── Login.vue        # 登录/注册
│   │   │   ├── Dashboard.vue    # 自选股 + 搜索
│   │   │   ├── StockDetail.vue  # 行情 + K线 + AI 分析
│   │   │   ├── Portfolio.vue    # 持仓管理（路线图）
│   │   │   ├── Alerts.vue       # 价格预警（路线图）
│   │   │   └── Profile.vue
│   │   └── router/              # 路由
│   ├── vite.config.js           # vite-plugin-pwa 配置（路线图）
│   └── package.json
├── python-data-service/         # AI 数据服务
│   ├── app.py                   # FastAPI 入口
│   ├── akshare_client.py        # 行情数据源
│   ├── quant_model.py           # 技术指标 + LGB 三窗口
│   ├── deep_models.py           # Transformer + DQN
│   ├── backtest.py              # 回测引擎
│   ├── finrl_demo.py            # FinRL PPO 集成
│   ├── mlflow_utils.py          # 实验追踪
│   ├── llm_service.py           # DeepSeek 客户端
│   ├── models/                  # 训练好的模型（gitignore）
│   └── prompts/                 # LLM prompt 模板
├── src/
│   └── main/java/com/happyericsix/stocktracker/
│       ├── cache/               # Caffeine + Redis 二级缓存
│       ├── client/              # 行情 API 客户端
│       ├── config/              # 安全 / Redis / WebClient 配置
│       ├── controller/          # REST 控制器
│       ├── service/             # 业务逻辑
│       ├── security/            # JWT 鉴权
│       └── repository/          # 数据访问
├── docker-compose.yml
├── pom.xml
└── README.md
```

---

## 缓存架构

```
请求 → ① Caffeine（本地，25s TTL）
        ├── 命中 → 直接返回
        └── 未命中 → ② Redis（分布式，30s TTL）
                       ├── 命中 → 回填 Caffeine → 返回
                       └── 未命中 → ③ 调数据源 → 回填两级缓存 → 返回
```

启动时自动清理 Redis 脏数据。

---

## 路线图

### ✅ 已完成
- 三窗口 LGB 训练管线（离线脚本）+ Transformer/DQN（默认下线）
- 模型持久化（磁盘 pkl）
- 按需 / 脚本重训（无后台定时调度）
- 时序切分防泄漏
- MLflow 实验追踪
- DeepSeek LLM 集成
- 二级缓存 + JWT 鉴权
- Docker Compose 部署
- Strategy Agent：自然语言生成策略 JSON，支持回测与模拟盘

### 🚧 进行中：PWA 化（接下来重点）
- [ ] vite-plugin-pwa 集成
- [ ] Web App Manifest（图标 / 主题色 / 启动画面）
- [ ] Service Worker 离线缓存
- [ ] Web Push 价格预警（前端 + 后端 + 推送服务）
- [ ] 移动端 UI 优化（响应式 + 手势）

### 🔜 下一阶段
- [ ] 持仓成本分析（用户绑定买入价，盈亏可视化）
- [ ] 回测结果前端可视化（夏普 / 最大回撤 / 收益曲线）
- [ ] 模型表现看板（R² / 胜率 / 信号准确率时间序列）
- [ ] 财报/新闻情感分析（RAG + LLM）
- [ ] 自动化周报推送

---

## 风险提示

本项目：
- ❌ 不接入实盘交易，**不会帮你下单**
- ❌ 不保证任何预测的准确性
- ❌ 不能替代你的投资判断

请把模型输出当作**一个参考意见**，而不是交易指令。投资有风险，决策需谨慎。

---

## License

MIT
