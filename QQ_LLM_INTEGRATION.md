# QQ 机器人 + LLM 集成方案

> 状态: **MVP 已实现，待端到端联调**
> 创建时间: 2026-08-03

## 架构概览

```
┌──────────────────────────────────────────────────────────┐
│  用户 (QQ)                                                │
│    ↓ 发消息                                               │
│  NapCat / Lagrange.OneBot (localhost:8081)                │
│    ↓ HTTP POST /qq_msg                                    │
│  python-data-service (FastAPI, localhost:8000)            │
│    ├── intent_router  →  解析意图                          │
│    ├── user_context   →  通过 QQ 查 user                   │
│    ├── akshare_client →  行情/K线                          │
│    ├── quant_model    →  技术指标 + ML 预测                │
│    └── llm_service    →  DeepSeek API (人话化输出)         │
│    ↓ 调内部接口查 user data                                │
│  Spring Boot (localhost:8080)                             │
│    └── /api/v1/internal/user/**  (X-Internal-Token)       │
└──────────────────────────────────────────────────────────┘
```

## 改动清单

### 后端 (Spring Boot)
- `dto/BindQqRequest.java` - 重写 (qqId + code 字段)
- `dto/BindQqResponse.java` - 新增 (绑定状态响应)
- `exception/BusinessException.java` - 新增 (业务异常)
- `exception/GlobalExceptionHandler.java` - 新增 BusinessException 处理
- `service/AuthService.java` - 修改 (注册时不再把 username 写到 qqNumber)
- `service/UserService.java` - **新建** (生成验证码/验证绑定/查询/解绑)
- `controller/UserController.java` - **填实** (4个公开接口 + 3个内部接口)
- `config/SecurityConfig.java` - 修改 (开放 /api/v1/internal/**)
- `application.properties` - 新增 (internal.api.token 配置)

### Python (FastAPI)
- `llm_service.py` - **新建** (DeepSeek 客户端 + prompt 加载)
- `intent_router.py` - **新建** (规则 + 关键词意图识别)
- `user_context.py` - **新建** (调后端 internal API)
- `qq_handler.py` - **新建** (按意图路由处理)
- `prompts/stock_analyst.md` - **新建** (股票分析 system prompt)
- `prompts/chat.md` - **新建** (闲聊 system prompt)
- `app.py` - 修改 (重构 /qq_msg + /health 端点)
- `test_intent.py` - **新建** (15 个单元测试)

### 前端 (Vue 3)
- `api/user.js` - **新建** (绑定相关 API)
- `pages/BindQQ.vue` - **新建** (绑定页面：生成验证码 + 步骤说明)
- `router/index.js` - 修改 (新增 /bind-qq 路由)
- `pages/Dashboard.vue` - 修改 (header 加「绑定QQ」按钮)

## 数据库

**已有字段** `users.qq_number`（unique）— 不需要改 schema

## 配置

### 后端
环境变量（application.properties 已带默认值）：
- `INTERNAL_API_TOKEN` - 服务间调用的 token，默认 `stock-tracker-internal-2026`
- `MYSQL_USER` / `MYSQL_PASSWORD` - 数据库账号

### Python
环境变量：
- `DEEPSEEK_API_KEY` - **必填** (去 https://platform.deepseek.com 申请)
- `DEEPSEEK_BASE_URL` - 默认 `https://api.deepseek.com`
- `DEEPSEEK_MODEL` - 默认 `deepseek-chat`
- `SPRING_BASE_URL` - 默认 `http://localhost:8080`
- `INTERNAL_API_TOKEN` - 必须与后端一致

## 启动顺序

```bash
# 1. MySQL + Redis
docker run -d --name stock-mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=123456 -e MYSQL_DATABASE=stockdb mysql:8
docker run -d --name stock-redis -p 6379:6379 redis:7-alpine

# 2. Spring Boot (IDEA 启动 StocktrackerApplication)

# 3. Python data service
cd python-data-service
export DEEPSEEK_API_KEY=sk-xxxx  # 填你的 key
python -m uvicorn app:app --reload --port 8000

# 4. NapCat / Lagrange.OneBot
# 启动后配置 webhook 指向 http://localhost:8000/qq_msg

# 5. 前端
cd frontend
npm install && npm run dev
```

## 用户使用流程

### 首次使用

1. 用户打开前端 `http://localhost:5173`
2. 注册账号 → 登录
3. 点头部「🔗 绑定QQ」按钮
4. 点「生成验证码」→ 拿到 6 位数字（如 `888888`）
5. 打开 QQ，给机器人发：`绑定 888888`
6. 机器人回复「✅ 绑定成功」

### 日常使用

在 QQ 给机器人发：

| 输入 | 效果 |
|------|------|
| `600519` / `茅台` | 实时行情 |
| `宁德能买吗` | 技术分析（LLM 解读） |
| `002594 k线` | 历史K线 |
| `我的自选` | 自选股今日表现 |
| `帮助` | 命令菜单 |
| `解绑` | 查看当前绑定（真正解绑需到网页） |
| `你好` / `你是谁` | 闲聊 |

## 端到端测试步骤

### 1. 单元测试（已完成 ✅）
```bash
cd python-data-service
python test_intent.py
# 应输出: 15/15 passed
```

### 2. 后端 API 冒烟测试
```bash
# 登录拿 token
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"testuser","password":"testpass"}'

# 生成验证码
curl -X POST http://localhost:8080/api/v1/user/bind-qq/code \
  -H "Authorization: Bearer <token>"

# 查询绑定状态
curl http://localhost:8080/api/v1/user/bind-status \
  -H "Authorization: Bearer <token>"

# 内部接口（Python 模拟）：验证绑定
curl -X POST http://localhost:8080/api/v1/internal/user/bind-qq \
  -H "Content-Type: application/json" \
  -H "X-Internal-Token: stock-tracker-internal-2026" \
  -d '{"qqId":"12345678","code":"888888"}'
```

### 3. Python 服务冒烟测试
```bash
# 健康检查
curl http://localhost:8000/health
# 应返回: {"status":"ok","llm_available":true,"llm_model":"deepseek-chat"}

# 模拟 QQ webhook
curl -X POST http://localhost:8000/qq_msg \
  -H "Content-Type: application/json" \
  -d '{"user_id":"12345678","message":"茅台"}'
```

### 4. 完整链路
1. 启动所有服务
2. 前端生成验证码
3. 模拟 NapCat 发 webhook 到 Python
4. 验证 Python 调用后端 /internal/user/bind-qq 成功
5. 验证 `users.qq_number` 已写入数据库
6. 解绑时反向走一遍

## 已知限制

- **自选股在 QQ 里查不到**：后端 `/api/v1/stocks/favorites` 需用户 JWT，Python 走 service-to-service 鉴权未实现。当前 `get_user_watchlist` 返回空，需要：
  - 方案A：后端加一个 internal 接口，直接通过 username 查自选股（不走缓存）
  - 方案B：用户绑定时让 Python 拿到 userId + 一个长效 token，存到 Redis
- **未做限流**：单用户/单 IP 调 LLM 没有速率限制，可能被刷
- **未做多轮对话**：每条 QQ 消息独立，不带上下文
- **未做日志持久化**：只在 stdout 打印

## 下一步（可选）

按优先级：
1. **P0**: 把自选股查通（方案A最简单）
2. **P1**: 加 LLM 调用限流（Redis 计数）
3. **P1**: 加多轮对话（Redis 存最近 5 条）
4. **P2**: 接入 FinRL 量化模型做策略推荐
5. **P2**: 自动周报 / 财报情绪分析

## 备份

实施前已备份：
- Git tag: `before-llm-20260803-104406`
- 物理副本: `E:\GitHubjob\Stock Tracker\_backup_before_llm_20260803-104410\`
