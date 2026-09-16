# 新闻/资讯模块 施工计划（搜索 + 分析优先）

> 日期：2026-09-14 · 主线：**新闻内容的搜索与分析**
> 设计依据：`docs/superpowers/specs/2026-09-05-news-module-design.md`（下文简称 spec）
> 路线依据：`docs/ROADMAP.md`
> 事实依据：本计划中所有接口字段、代码约定、文件路径均**实测核对过**（见 §0.2 实测记录），不是照抄 spec 的假设。

---

## 0. 开工前必读

### 0.1 产品形态已确认：盘前/盘后简报 + 盘中漏斗（本节优先于其他分期讨论）

用户明确的四个诉求：

1. 每天**开盘前 / 收盘前**拿到自选股相关的新闻；
2. AI 帮忙**解读、总结、分析利好**；
3. **随时关注市面上的股票**，对用户提醒警告；
4. **国家/行业总体环境**的大体判断。

这四条把模块的**主形态**从"用户主动搜索"改成了"**系统主动送达**"。由此确定三层架构（详见 §0.6）与任务顺序：

**因此任务顺序修订为：**

```
N0 地基 → N1 取数 → N2 分析 → N3 落库+搜索API+漏斗
   → N5a 盘前/盘后简报 Job（复用消息中心，零新前端）★ 核心诉求，提前
   → N4 搜索页与分析卡片（用户想主动查时的入口）
   → N5b 个股页事件时间轴 + Dashboard 汇总卡
   → N6-1 对话式问答（扩 3 个 Agent 工具）→ N6 其余可选
```

**为什么 N5a 反而排在 N4（搜索页）之前**：简报**只是一条 `type=NEWS` 的消息**，复用现有消息中心与 SSE 推送，**边际前端成本为零**；而搜索页是一整页新 UI。同样的用户价值，简报的投入小一个量级，且它才是"每天都会发生的使用场景"。

> **相对 spec §11 的有意调整（保留）**：spec 把「搜索/解读」放 P1、「事件雷达+汇总」放 P0。本计划仍坚持**先打通取数→抽取→落库（N1–N3）**——因为搜索页和简报**共用同一套底层能力**，先做底层能让两者都不返工；分歧只在"底层通了之后先交付哪个"，答案是先交付简报（见上）。

### 0.2 实测记录（2026-09-14，akshare 1.18.64，真实网络）

`python-data-service/_probe_news.py` 实测结果（**可直接依赖的字段名**）：

| 接口 | 实测行数 | 实测列名 |
|---|---|---|
| `ak.stock_notice_report(symbol="全部", date="20260914")` | **469**（当日全市场公告） | `代码, 名称, 公告标题, 公告类型, 公告日期, 网址` |
| `ak.stock_news_em(symbol="600519")` | 10 | `关键词, 新闻标题, 新闻内容, 发布时间, 文章来源, 新闻链接` |
| `ak.stock_research_report_em(symbol="600519")` | 771 | `序号, 股票代码, 股票简称, 报告名称, 东财评级, 机构, 近一月个股研报数, 2026-盈利预测-收益, 2026-盈利预测-市盈率, …(2027/2028 同构), 行业, 日期, 报告PDF链接` |
| `ak.stock_news_main_cx()` | 100 | `tag, summary, url` |

实测样本（确认字段语义）：
- 公告：`{代码: 300623, 名称: 捷捷微电, 公告标题: "捷捷微电:…关于调整2026年限制性股票激励计划相关事项的公告", 公告类型: 股权激励进展公告, 公告日期: "2026-09-14", 网址: "https://data.eastmoney.com/notices/detail/300623/AN2026….html"}`
- 个股新闻：`{新闻标题: "贵州茅台600519.SH)：2026年中报净利润为445.17亿元…", 发布时间: "2026-08-15 10:11:51", 文章来源: 界面新闻, 新闻链接: "http://finance.eastmoney.com/a/….html"}`
- 研报：`{报告名称: "2026年中报点评：茅台酒稳健，系列酒主动调整", 东财评级: 买入, 机构: 西南证券, 日期: "2026-08-21", 报告PDF链接: "https://pdf.dfcfw.com/…"}`
- 财新要闻：`{tag: 交易簿, summary: "对这场算力投资军备竞赛而言…", url: "https://database.caixin.com/…"}`

**三条实测结论**：
1. ⚠️ `公告日期` 是**字符串** `"2026-09-14"`（不是 date 类型），`发布时间` 是 `"YYYY-MM-DD HH:MM:SS"` —— 落库前统一 `parse_datetime()`，别直接 `LocalDateTime.parse`。
2. ⚠️ **`stock_news_em` 只返回 10 条**（东财个股新闻页就这么多），**不能当"该股全部新闻"用**；要历史/更多需靠每日增量落库累积。这直接决定了 N3「库优先 + 实时兜底」的搜索策略是必须的，而不是可选优化。
3. ⚠️ 研报接口返回 771 行且含三年盈利预测 —— 单股全量落库**不划算**，研报只按需（用户点开时才拉）+ 只保留 `days` 窗口。

### 0.3 环境实测（与施工直接相关）

| 事实 | 影响 |
|---|---|
| venv 里 `chromadb` / `lightgbm` / `mlflow` / `sklearn` / `httpx` / `pytest` **都已安装** | N1/N2 无需装依赖 |
| 但 `requirements.txt` **没写 `chromadb`**，且注释乱码导致 `lightgbm>=4.0.0` **被并进注释行**（`# ML 妯″瀷(...)闇€瑕?lightgbm>=4.0.0`）→ `pip install -r requirements.txt` 装不上 lightgbm | N0 顺手修 requirements.txt |
| `python-data-service/` 目录下**文件写入被拒绝**（`_probe_news_schema.json`、`.pytest_cache` 均 Permission denied），但仓库根与源码可写 | 脚本**不要往该目录写运行时文件**；落盘一律走 Java/DB，临时产物走 `$env:TEMP` |
| `pytest tests` 当前 **5 failed / 40 passed**，失败全是 401（`app.py` 的 `x-internal-token` 中间件 + 本地 `.env` 配了 token，测试没带头） | N0 必须加 `tests/conftest.py`，否则**新写的新闻测试会踩同一个坑** |
| 控制台是 GBK，中文列名直接 print 会乱码 | 运行脚本加 `$env:PYTHONIOENCODING="utf-8"` |
| 前端 `vite.config.js` 的 Workbox 对 `/api/*` 用 `NetworkFirst` + **`maxAgeSeconds: 300`** | 新闻是时效内容，5 分钟缓存会让"新公告不出现"；N4 必须给 `/api/v1/news` 加 `NetworkOnly` 规则 |

### 0.4 已核实的代码约定（**照抄这些，不要自创**）

**Python（`app.py`）**
- 所有 `/api/v1/*` 端点都在 `require_internal_token` 中间件后面，需要 `x-internal-token` 头。
- 端点写法：`async def`，重活用 `await asyncio.to_thread(...)`（见 `backtest_strategy_endpoint`），模块 lazy import（`from agent.xxx import ...` 写在函数体内）。
- 错误处理：`try/except` + `logger.error(f"... error: {e}", exc_info=True)` + 返回**中文可读**的兜底结构，不抛 500。两种返回形状并存：
  - 策略类：`{"valid": False, "error": "…"}`
  - 同花顺类：`{"ok": True, "data": …}` / `JSONResponse(status_code=502, content={"ok": False, "error": "…"})`
  - **新闻模块统一用 `{"ok": bool, "data": …, "error": …}` 形状**（与 ths 对齐，前端已熟悉）。
- LLM 统一走 `llm_service.chat_completion(messages, tools=None, temperature=0.2, max_tokens=1200)`；prompt 模板放 `prompts/*.md`（现有 `chat.md`/`stock_analyst.md`，由 `_load_prompt(name)` 加载）。

**Java（8 条硬约定，都是实测出来的坑，照抄不要自创）**
1. **Jackson 是 Jackson 3**：`import tools.jackson.databind.JsonNode;` / `tools.jackson.databind.ObjectMapper`（**不是** `com.fasterxml.jackson.databind`）；只有注解仍是 `com.fasterxml.jackson.annotation.JsonIgnore`。
2. **`Result.success("删除成功")` 走的是 `success(T data)` 重载** → 字符串进 `data` 字段、`message` 仍是 `"success"`。要自定义 message 必须 `Result.success("操作成功", data)`。
3. **带默认值的字段必须 `@Builder.Default`**，否则 `builder().build()` 得到 null，撞 `nullable=false` → `DataIntegrityViolationException` → 被翻译成 409。可选字段只在非 null 时 set 进 builder（`AlertService.addAlert` 有原注释警告）。
4. **`X-Internal-Token` 不是全局 filter**，是每个 client 在自己构造函数里 `builder.defaultHeader("X-Internal-Token", internalToken)`，且**必须先判空**（空则不加头，方便本地免鉴权）。
5. **超时挂在每个 Mono 上**：`.bodyToMono(X.class).timeout(Duration).block()`。既有取值：普通数据接口 15s、LLM/回测 60s、轮询 10s。
6. **两套异常范式必须显式选一**：`AkshareStockClient` 吞异常返回降级对象（行情类，"失败不能让预警任务挂"）；`ThsClient` 抛 `RuntimeException(中文)`（"否则『凭证失效』和『本来就是空的』分不清"）。判据 = **是否需要区分"空结果"与"失败"**。
7. **Service 不返回 `Result`、不 catch 成 HTTP**：抛 `IllegalArgumentException("xxx不存在")`（→400）或 `BusinessException(code, msg)`，由 `GlobalExceptionHandler` 统一翻译；**调 Python 的长阻塞方法不要包在 `@Transactional` 里**（`StrategyService.runBacktest` 有原注释：60s 阻塞 HTTP 不该占事务）。
8. **测试是 JUnit 5 + Mockito 纯单元测试**（`@ExtendWith(MockitoExtension.class)` + `@Mock` + `@InjectMocks`），**不开 Spring 容器、不用 MockMvc**；`src/test/resources/application.properties` 用 H2，且**没有 `internal.api-token`**（靠 `@Value` 默认值 `""` 兜住）。
9. **`spring.cache.cache-names` 与 `RedisCacheConfig.CACHE_NAMES` 要同步改**（两处），否则新增缓存名直接报错 —— 新闻模块**不加缓存**（时效性内容，缓存只会带来"看不到新公告"的 bug）。
10. **Controller 用 `StrategyController` 的风格**（全量 `Result<T>`），不要跟 `AlertController`（GET 返回裸 DTO）混。

**前端**
- 统一响应包装 `dto/Result.java`：`Result.success(data)` → `{code:200, message:"success", data:…}`。
- 前端 axios 拦截器**不剥壳**（`response => response`），所以前端取值用 `Strategies.vue` 的 `unwrap(res)` 通吃两种形态。

**前端（Vue 3 `<script setup>`，无 UI 库、无 pinia 实效、全手写 CSS）**
- `src/api/xxx.js`：一行一个箭头函数导出，参数用 `({ page = 0, size = 20, symbol } = {})` 可选参数写法（照 `api/messages.js`）。
- 取值一律 `unwrap(res)`（照抄 `Strategies.vue:22-26`），不要写死 `res.data.data`。
- 错误：**每页自持 `error` ref + `<p v-if="error" class="error">`**，catch 里走 `errorMessage(e, '默认文案')`；401 已由 `request.js` 统一处理，不要重复写。
- 列表页三态：`v-if="error"` → `v-else-if="loading"` → `v-else-if="list.length===0"` → `v-else`；分页+滚动加载只有 `Messages.vue` 实现过，照抄它。
- 弹窗照抄 `.modal-mask` + `.modal-box`（含 `@click.self` 关闭）。
- **落点已核实**：个股页是 `src/pages/KLineChart.vue`（路由 `/chart/:symbol`，**不存在 `StockDetail.vue`**）；归因对照条插在 `<main>` 第一个子元素（`.chart-container` 之前），事件时间轴插在 `.price-info` 之后；Dashboard 汇总卡插在 `syncMsg` 与 `.search-section` 之间，复用 `.ths-card` 的左色条卡片样式。
- 色板（沿用现有字面量）：主色 `#1677ff`、深色 header `#1a1a2e`、页面底 `#f0f2f5`、涨/利好 `#ff4d4f`、跌/利空 `#52c41a`、警告 `#fa8c16`。
- 触达：`messageBus.js` 的 SSE 通道 `/api/v1/messages/stream?token=…` 已存在；**重大事件只需后端写成一条 `type=ALERT` 的 message，前端零改动即可显示**。

---

### 0.5 架构决策：哪里该用 Agent、哪里不该（重要，先定这个再写代码）

**结论：本模块只有一处该用 Agent（对话式问答 + 入口路由），其余全部是确定性管道 + 单次结构化 LLM 抽取。不要建"总调度 Agent + 子 Agent"。**

| 任务 | 正确形态 | 为什么不是 Agent |
|---|---|---|
| 取数 / 去重 / 分级 / 落库（N1、N3） | **确定性管道**：固定顺序的函数调用 | 调哪个 akshare 接口、按什么字段映射，由**参数**决定，不由 LLM 决定。没有路由不确定性，就没有 Agent 的用武之地 |
| 判断利好/利空、影响强度、"意味着什么"（N2） | **单次结构化 LLM 抽取**：固定 JSON schema（spec §6），一次调用批量出结果 | 这是**分类**任务不是**推理**任务。强制单次结构化输出比让模型自由多步推理**更不容易幻觉**——而 `direction` 正是本模块最不能错的字段 |
| 倾向分计算 / 是否已被价格反映 | **确定性加权 + 跨模块数据拼接**（见下） | 可复现、可解释、可单测；让 LLM 直接说"利好 7 分"既不可复现也无法审计 |
| 用户问"为什么今天跌""这条公告对茅台意味着什么" | ✅ **ReAct Agent**（**扩展现有** `agent/react_agent.py` 的工具集） | 问题空间开放，需要模型自己决定查行情/查指标/查事件、查几轮 |
| 入口意图路由（问候 / 查行情 / 做策略 / 问事件） | ✅ **复用现有唯一入口** `/api/v1/agent/chat` + 其 ReAct loop | spec §2 已定："全部聊天消息统一进入 Agent（替代 `parse_intent` 固定分发）"——入口只有一个，不要再包一层 |

**为什么"总调度 Agent 调子 Agent"现在不该做**（5 条具体理由）：

1. **每一层 Agent 都要一次 LLM 往返** → 延迟与 token 成本按层数翻倍。新闻是 469 条/日量级，Agent 化后成本差两个数量级。
2. **子 Agent 之间只能用自然语言通信** → 丢失结构化契约。本项目最值钱的设计恰恰是"策略 JSON 是唯一契约"；新闻也应该有"事件 JSON 契约"（spec §6），Agent 之间传文本会把这份契约腐化掉。
3. **幂等与重试会失效**。管道必须"失败可重跑、断点可续"，而 ReAct loop 每次的步骤序列可能不同 → 去重键、重试边界全变复杂。现有 Agent 已有 `MAX_STEPS = 6` 的硬上限，本身就说明它的输出不确定。
4. **可测试性断崖式下降**。确定性管道能 mock akshare 断言字段映射（N1 的单测就是这么写的）；Agent 只能做行为测试，**又慢又烧钱**，无法进 CI。
5. **当前只有 2 个域（策略、新闻），路由复杂度还没到需要独立调度器的程度**。多一层调度的收益（解耦）远小于成本（延迟、成本、调试难度）。

**什么时候才真需要多 Agent**（给未来留的判据，不是现在做）：域 ≥ 4 个、且每个域内有多个需要不同工具集与不同 system prompt 的专属子任务时。届时正确写法仍然是**"域路由（一次 LLM 调用）+ 域内确定性服务"**，而不是 Agent 调 Agent。

**"计算利好"的正确形态（确定性加权，不是让 LLM 打分）**

```
LLM 只负责三件分类事：direction(利好/利空/中性) · confidence(0~1) · impact_level(high/medium/low)
代码负责算分（可复现、可解释、可单测）：
    raw_score   = direction_sign(+1/-1/0) × confidence × impact_weight(impact_level)
    source_trust = {1公告: 1.0, 2媒体: 0.7, 3研报: 0.8, 4舆情: 0.4}
    weighted    = raw_score × source_trust[source_level]
    # 再由代码判定"是否已被价格反映"：把事件日期与 get_history 的当日/次日涨跌幅对照
    reflected   = |事件后 N 日累计涨跌| 与 weighted 同向且幅度超过阈值 → "股价已提前反应"
```

这样拆分的好处：`direction` 错了能一眼定位是分类问题；权重不合理只改常量不改 prompt；"是否已反映"这个跨模块判断（新闻 × 行情）恰好是**需要 Agent 的那部分**——用户问起来时由 Agent 调 `search_news` + `get_history` 现场推。

**对施工图的影响**：N1/N2/N3 完全不变（它们本来就是确定性管道）；N4 之后**新增 N6-1「对话式问答」**——只做一件事：往 `agent/tool_registry.py` 的 `TOOL_SCHEMAS` 里加 3 个新闻工具，让现有 Agent 顺带会答事件类问题。

---

### 0.6 主动送达架构（对应四个诉求）—— 以及 Agent 在其中的正当位置

#### 0.6.1 总体形态：调度 + 漏斗 + 聚合 + 生成，Agent 只做"增强层"

```
┌─ 调度层（Scheduler，确定性）────────────────────────────────────┐
│ 08:30 盘前简报 Job     15:40 盘后简报 Job     盘中：随价格刷新触发 │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 采集层（N1，确定性管道）───────────────────────────────────────┐
│ 全市场公告 469 条/日 · 自选股新闻 · 研报 · 财新要闻（宏观）        │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 漏斗层（N3，纯代码，成本控制的关键）────────────────────────────┐
│ L1 规则命中：股票代码 ∈ 自选/持仓  或  关键词表命中                │
│    （关键词表：减持/增持/处罚/立案/重组/业绩预告/回购/停牌/退市…）  │
│ L2 相关度打分：行业/题材/规模相关度（关键词权重或 embedding 相似度）│
│ L3 只对通过 L1+L2 的条目做 LLM 抽取（通常每天 50–150 条）          │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 理解层（N2，单次结构化 LLM 抽取，确定性 schema）────────────────┐
│ 每条 → event_type / direction / confidence / impact_level /       │
│        plain_summary / risks / opportunities（spec §6）           │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 聚合层（确定性）───────────────────────────────────────────────┐
│ 事件 + 当日行情 + 用户持仓/成本 + 宏观与行业环境 → 结构化上下文    │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 生成层 ────────────────────────────────────────────────────────┐
│ ① 保底（确定性）：模板化简报（"今日 3 条：1) … 2) … 3) …"）       │
│ ② 增强（Agent，可选）：给 ReAct agent 一份任务 + 工具集，          │
│    让它自己决定是否再查行情/历史/同业，产出更有洞察的简报          │
│    失败/超时 → 自动回退到 ①（绝不让用户啥也收不到）               │
└───────────────┬─────────────────────────────────────────────────┘
                ▼
┌─ 触达层（复用现有，零新前端）───────────────────────────────────┐
│ MessageService.saveAndPush(user, "NEWS", …) → 落库 + SSE 推送      │
│ impact=high 且 direction≠中性 → 走 Alert 通道即时提醒              │
└─────────────────────────────────────────────────────────────────┘
```

#### 0.6.2 四个诉求分别怎么落地

| 诉求 | 落地方式 | 用 Agent 吗 |
|---|---|---|
| ① 开盘前/收盘前拿到自选相关新闻 | `PrepBriefJob`（08:30）+ `CloseBriefJob`（15:40），只查自选/持仓 | ❌ 调度 + 查询 |
| ② AI 解读 / 总结 / 分析利好 | 逐条 LLM 结构化抽取（N2）+ 简报生成（单次生成，固定上下文） | ❌ 分类 + 单次生成 |
| ③ 随时关注全市场并提醒 | 三层漏斗 + `impact=high` 才走 Alert 即时推送 | ❌ 规则 + 阈值 |
| ④ 国家/行业总体环境 | 财新要闻（宏观）+ 行业研报按**行业**聚合 | ❌ 聚合 + 单次生成 |
| ⑤ **"我该注意什么"这类开放判断** | **增强层 Agent**：对高影响事件自主追查行情/历史/同业后再写简报 | ✅ **这里才是 Agent** |
| ⑥ 用户追问"为什么今天跌" | **N6-1** 扩 3 个工具给现有 Agent | ✅ |

**关键区分**：①–④ 的**输入是可知的**（自选股有哪些、有哪些事件），所以能用确定性管道做，且必须用管道做（成本/幂等/可测）；⑤⑥ 需要模型**自己决定还要查什么**，输入不可预知——这才是 Agent 的价值所在。

#### 0.6.3 为什么"全市场关注"必须走漏斗（成本算给你看）

- 全市场约 5400 只股票；若全量做 LLM 抽取，每天光公告就 469 条 + 各家新闻，**成本随市场而非随用户增长**，且 99% 与用户无关。
- 漏斗后的真实量级：20 只自选 → 事件约 100–400 条/日 → L1+L2 过滤后 **50–150 条** → 批量 5 条/次 ≈ **10–30 次 LLM 调用/日** + 2 次简报生成。
- 所以 L1 **必须是代码规则**，绝不能是"让 LLM 判断这条与我有关吗"——那是把成本乘 100 倍。

#### 0.6.4 打扰分级（防止变成"资讯轰炸"）

**这是用户自己 spec §1.2 定的反目标**：*"不做 24 小时资讯轰炸 / 瀑布流信息流（对散户价值低甚至有害）"*。因此必须三级分流：

| 级别 | 条件 | 触达方式 |
|---|---|---|
| 🔴 即时打扰 | `impact_level=high` **且** `direction≠中性` **且** 标的是自选/持仓 | 走 Alert 通道即时推送（用户已熟悉的通道） |
| 🟡 定时送达 | 与自选/持仓相关，但不满足上面的门槛 | 攒进盘前/盘后简报（一天最多 2 次） |
| ⚪ 只入库 | 全市场其余事件 | 只落库，**不推送**，用户主动搜索时才看到 |

**没有这个分级，"随时关注全市场"就会退化成用户明确反对的那个东西。**

#### 0.6.5 诉求 ④「行业环境」需要补一块数据

实测发现 `stock_research_report_em` 返回**行业字段**（样本：贵州茅台 → `白酒Ⅱ`），但 `favorite_stocks` **没有行业列**。因此：

- **首选**：从研报接口的行业字段回填用户的行业归属（研报按股票查，天然带行业），只在同步/首次需要时调一次，结果缓存。
- **兜底**：用 `get_overview` 的行业字段（Python 侧已有 `get_overview`）。
- **行业环境段落**：把「宏观要闻（财新）+ 该行业近期研报观点 + 该行业相关公告」交给一次 LLM 生成，输出"大体环境"段落——**这是单次生成，不是 Agent**。

#### 0.6.6 对 N5 任务的修订

原 N5 只有"个股页事件区 + Dashboard 汇总卡 + 一个收盘 Job"。按 §0.6 修订为：

- **N5a（提前）**：`PrepBriefJob`（08:30）+ `CloseBriefJob`（15:40），产出 `type=NEWS` 消息 → **零新前端**；含漏斗（L1/L2）、打扰分级、模板化保底简报；增强层 Agent 先留开关默认关（先用确定性简报验证数据质量）。
- **N5b（其后）**：个股页事件时间轴 + 归因对照条 + Dashboard 汇总卡。
- **增强层 Agent（N5a 稳定后开）**：只对 `impact=high` 的事件做自主追查，失败回退模板简报。

---

## N0 施工前地基（0.5 天）

**目标**：让新代码有干净的落点，别把新闻代码和「101 项未提交改动」搅在一起。

- [ ] **N0-1** 提交当前工作区（按主题拆 commit，至少把 THS 功能与 spec 文档入库），保证新闻模块的 diff 是独立可审的。
- [ ] **N0-2** 新增 `python-data-service/tests/conftest.py`：提供带 `x-internal-token` 的 `TestClient` fixture，并让现有 `tests/test_endpoints.py` 复用 → 目标 `pytest tests` **全绿**。
  - 关键：fixture 读 `app.INTERNAL_API_TOKEN`，测试里 monkeypatch 成固定值或直接用同一值带头。
- [ ] **N0-3** 修 `requirements.txt`：按 UTF-8 重存、补 `chromadb`（N6 用）、把被注释吞掉的 `lightgbm>=4.0.0` 还原成独立行。
- [ ] **N0-4** 删除本计划的临时探针 `python-data-service/_probe_news.py`（其结论已固化到 §0.2），或移进 `docs/` 作为附录。

**验收**：`pytest tests` 全绿；`git status` 只剩新闻模块的新文件。

---

## N1 取数管道 `news_client.py`（1–2 天，纯取数、无 LLM）

**文件**：新建 `python-data-service/news_client.py`、`tests/test_news_client.py`（单测）、`test_news_client_live.py`（真实链路脚本，照 THS 的脚本验证传统）。

**接口设计**（全部返回**统一的内部事件结构**，字段名与 spec §6 对齐）：

```python
# ---- 内部事件结构（落库与 LLM 抽取共用同一份键名）----
# {
#   "symbol": "SH600519" | "",        # 归一化代码；宏观新闻为空
#   "name": "贵州茅台" | "",
#   "title": "…",
#   "content": "…",                    # 摘要/正文（公告无正文，用标题）
#   "url": "…",                        # 去重主键
#   "source_level": 1|2|3|4,           # 1公告 2媒体 3研报 4舆情
#   "source_name": "东方财富/界面新闻/西南证券/…",
#   "event_type_raw": "股权激励进展公告",   # 源站自带分类（公告类型/财新tag）
#   "published_at": "2026-09-14 10:11:51" # 统一成字符串，落库前解析
# }

def fetch_announcements(day: str = "") -> list[dict]      # day="YYYYMMDD"，默认今天
def fetch_stock_news(symbol: str) -> list[dict]           # ⚠️ 最多 10 条（见 §0.2）
def fetch_research_reports(symbol: str, days: int = 30) -> list[dict]
def fetch_market_news() -> list[dict]                     # 财新要闻，宏观
def search_news(symbol: str = "", keyword: str = "",
                types: list[int] | None = None, days: int = 7) -> dict
def dedup(items: list[dict]) -> list[dict]
def normalize_url(url: str) -> str                        # 去 query/尾斜杠/统一小写 host
```

**实现要点**
1. 每个 `fetch_*` **失败必须返回 `[]` 并 `logger.warning`**，绝不抛异常 —— 单一信源挂掉不能让整个搜索失败（与 spec §13 风险表一致）。
2. 字段映射集中在一个 `_MAP` 常量表里（照 `ths_client.py` 把常量集中在文件头部的惯例），接口改版只改一处。
3. `dedup` 两级：① `normalize_url(url)` 相同即重复；② 公告无 url 时用 `(symbol, title, published_at)` 兜底（spec §7 去重键）。
4. `search_news` 是**N1 的唯一对外入口**：按 `types` 决定调哪些源，聚合 → `dedup` → 按 `published_at` 倒序 → 截 `days` 窗口 → 返回 `{"items": [...], "counts": {"notice": n, "news": n, "report": n}, "errors": ["研报源超时"]}`。
5. `symbol` 归一化**复用** `akshare_client.normalize_symbol`（腾讯格式 `sh600519`）→ 输出统一 `SH600519`（与 `ThsSyncService.bareCode` 的前缀语义保持一致）。
6. 拉取全市场公告时**必须 `asyncio.to_thread`**（469 行 + 网络，会阻塞事件循环）。

**Python 端点**（加在 `app.py` 末尾，新版块注释 `# ==================== 新闻/资讯 ====================`）：

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/v1/news/fetch` | `{"symbol":"600519","keyword":"","types":[1,2,3],"days":7}` | `{"ok":true,"data":{"items":[…],"counts":{…},"errors":[…]}}` |

**验收**
- 真实链路脚本跑通：`600519` 拿到公告 + 新闻 + 研报，条数与 §0.2 实测一致。
- 单测覆盖：字段映射（用实测样本做 fixture）、`normalize_url` 去重、某源抛异常时其余源照常返回。
- 端点手动 curl（带 `x-internal-token`）返回结构正确。

---

## N2 分析层 `news_understanding.py`（1–2 天）

**文件**：新建 `python-data-service/news_understanding.py`、`python-data-service/prompts/news_analyst.md`、`tests/test_news_understanding.py`。

**契约**：严格按 spec §6 的 schema 落地，**字段名一字不改**：

```json
{
  "event_type": "业绩预告|重大合同|回购|增减持|监管处罚|重组|宏观政策|行业动态|其他",
  "direction": "利好|利空|中性",
  "confidence": 0.0,
  "impact_level": "high|medium|low",
  "related_symbols": ["SH600519"],
  "source_level": 1,
  "plain_summary": "大白话一句话：发生了什么 + 对持有者意味着什么",
  "risks": ["…"],
  "opportunities": ["…"]
}
```

**接口**

```python
def analyze_events(items: list[dict]) -> dict          # 批量抽取，内部每批 ≤5 条
def analyze_one(item: dict, mode: str = "deep") -> dict # 单条深度解读（risks/opportunities 红绿标注）
def sanitize(text: str) -> str                          # 合规后置过滤（见下）
def extract_json(text: str) -> dict | None              # 通用容错 JSON 提取（含 ```json 围栏）
```

**实现要点**
1. **批量而非逐条**：469 条公告逐条调 LLM 既慢又贵；按 `impact` 预筛（`event_type_raw` 命中关键词表：减持/处罚/重组/业绩预告…）+ 每批 ≤5 条。
2. **降级链**（spec §6 约束 + §13 风险）：LLM 不可用（`llm_service._is_available()` 为假）→ 解析失败 → 超时，**三级降级都返回**：
   ```json
   {"direction": null, "confidence": 0.0, "impact_level": "low",
    "plain_summary": "信息不足，不判断方向", "risks": [], "opportunities": []}
   ```
   原文照常入库（**信息不足 ≠ 不入库**，只是不判方向）。
3. **合规后置过滤 `sanitize`**（spec §10）：正则剔除/改写"建议买入、建议卖出、可以加仓、立即清仓、必涨、稳赚"等投顾句式；每条 `plain_summary` 尾部统一附免责短句。**prompt 约束 + 后置过滤双保险**，不要只靠 prompt。
4. `direction` 语义写进 prompt 与 docstring：**是"影响方向"，不是"明日涨跌预测"**。
5. 温度取 0.1–0.2，`max_tokens` 要够（批量 5 条 × 字段），并**要求只输出 JSON**（复用策略 Agent 的"围栏 JSON"约定，`extract_json` 与 `agent/strategy_schema.extract_strategy_json` 保持同一套容错风格）。

**Python 端点**

| 方法 | 路径 | 请求 | 响应 |
|---|---|---|---|
| POST | `/api/v1/news/analyze` | `{"items":[{…}], "mode":"batch"\|"deep"}` | `{"ok":true,"data":{"items":[{…原始字段…, 分析字段}]}}` |

**验收**
- mock LLM：schema 字段齐全、非法 JSON 重试一次后降级、低置信返回 `direction=null`、`sanitize` 能拦住"建议买入"样本。
- 真实链路：拿 N1 抓到的真实公告跑一遍，输出 `plain_summary` 对散户可读、`risks/opportunities` 非空（对重大公告而言）。
- **人工质量抽查 10 条**：`direction` 误判率可接受（这是本模块最大风险，spec §13 已列）。

---

## N3 Java 落库 + 搜索 API（2–3 天）

**文件**
- 新建：`entity/NewsEvent.java`、`entity/NewsStockRel.java`、`repository/NewsEventRepository.java`、`repository/NewsStockRelRepository.java`、`client/NewsClient.java`、`service/NewsService.java`、`controller/NewsController.java`、`dto/NewsEventResponse.java`、`dto/NewsSearchRequest.java`。
- 修改：`config/SecurityConfig.java`（无需改，走默认 JWT 保护）、`application.properties`（如需新增新闻相关开关）。

**数据模型**（spec §7，字段名照抄）

```sql
news_events(id PK, symbol, name, title, content TEXT, url, source_level TINYINT,
            source_name, event_type, direction, confidence DOUBLE, impact_level,
            plain_summary TEXT, risks TEXT, opportunities TEXT,
            published_at DATETIME, created_at DATETIME)
news_stock_rel(id PK, event_id, symbol, INDEX(symbol, published_at))
```
- 去重键：`url` 唯一索引；公告无 url 时用 `(symbol, title, published_at)`。
- `risks`/`opportunities` 存 JSON 字符串（`TEXT`），读取时反序列化。

**服务职责**
- `NewsService.search(username, req)`：**库优先 → 不足则实时兜底**（关键，因 `stock_news_em` 只有 10 条）：
  1. 按 `symbol/keyword/types/days` 查 `news_events`；
  2. 若结果为空或 `days` 窗口内该 symbol 的最新事件早于今日 → 调 `NewsClient.fetch(...)` → `upsert` → 再查一次；
  3. 若请求里 `scope=mine`（汇总卡用）→ `JOIN favorite_stocks ON user_id` 过滤（spec §7 的"过滤条件天然来自自选/持仓"）。
- `NewsService.analyze(username, eventIds, force)`：**幂等**——已有 `plain_summary` 且非 `force` 则跳过；调 `NewsClient.analyze(...)` 回填。
- `NewsService.upsert(items)`：按 `url` 去重，**只增不改已分析内容**（重抓同一篇文章不应覆盖已生成的解读）。

**端点**（全部 JWT，返回 `Result.success(...)`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/news/search` | body `{symbol?, keyword?, types?, days=7, scope?, page=0, size=20}` → `Page<NewsEventResponse>` |
| GET | `/api/v1/news/events?symbol=&days=30` | 个股事件时间轴（时间倒序） |
| POST | `/api/v1/news/analyze` | body `{eventIds:[…], force?:false}` → 分析结果列表 |
| POST | `/api/v1/news/refresh?day=` | 手动触发当日增量（运维/调试用） |

**NewsClient**（照 `client/StrategyClient` / `AkshareStockClient` 的既有构造模式）

```java
@Service
public class NewsClient {
    private static final Duration REQUEST_TIMEOUT = Duration.ofSeconds(30);  // 抓全市场公告偏慢，比行情类 15s 放宽

    public NewsClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl,
                      @Value("${internal.api-token:}") String internalToken,
                      ObjectMapper mapper) {           // Jackson 3: tools.jackson.databind.ObjectMapper
        WebClient.Builder builder = WebClient.builder().baseUrl(baseUrl)
                .defaultHeader("Accept-Charset", "utf-8");
        if (internalToken != null && !internalToken.isBlank()) {
            builder.defaultHeader("X-Internal-Token", internalToken);   // 必须判空（约定 4）
        }
        this.webClient = builder.build();
        this.mapper = mapper;
    }

    public JsonNode fetch(String symbol, String keyword, List<Integer> types, int days) { … }
    public JsonNode analyze(List<JsonNode> items, String mode) { … }
}
```

- **异常范式选「`AkshareStockClient` 式降级 + 保留错误信息」**：Python 端已用 `{"ok":false,"error":"…"}` 表达失败，Java 侧 `catch (WebClientResponseException e)` → `log.warn` → 返回 `{"ok":false,"error":"资讯服务暂时不可用"}` 的 `JsonNode`，让 `NewsService` 能**同时**区分「库里有结果但刷新失败」与「彻底没结果」——这正好满足约定 6 的判据。
- 若新增 `news.api.base-url` 配置项，用 `${ENV:default}` 形式（比 `akshare.api.base-url` 的字面量形式更显式）；也可以直接复用 `akshare.api.base-url`（同源，同一 Python 进程）。

**实体与落库要点**
- `@Table(name = "news_events", uniqueConstraints = { @UniqueConstraint(columnNames = {"url"}) })`；`ddl-auto=update`，**没有 Flyway**，唯一约束只能靠注解表达。
- ⚠️ **`news_events` 是全局事实表，不加 `user_id`**。新闻/公告对所有用户是同一份事实，per-user 只体现在「看哪些」——通过 `scope=mine` 时 `JOIN favorite_stocks ON user_id` 过滤（spec §7 原话："过滤条件天然来自自选/持仓"）。加 `user_id` 会导致同一条公告被 N 个用户存 N 份，去重键也失效。
- `risks` / `opportunities` 用 `@Column(columnDefinition = "TEXT")` 存 JSON 字符串；`content`/`plain_summary` 同理。
- `createdAt`：`@Column(nullable = false, updatable = false)` + `@PrePersist` + **`if (createdAt == null)`** 兜底（照 `Message.prePersist`）。

**Controller / Service 约定**
- Controller 全量 `Result<T>`（照 `StrategyController`），方法签名带 `Authentication authentication`，`authentication.getName()` 取用户名。
- 写方法 `@Transactional`（public，跨 bean 代理），**查库 + 调 Python 兜底不包同一个事务**（拆成"先查 → 调 Python → 短事务落库"三步）。
- 错误一律抛：`throw new IllegalArgumentException("新闻不存在")`（→400）。

**测试（照约定 8）**：`@ExtendWith(MockitoExtension.class)` + `@Mock NewsEventRepository / NewsClient` + `@InjectMocks NewsService`，纯单测不开 Spring。

**验收**
- 单测：`upsert` 去重（同 url 不重复、重抓不覆盖已分析）、`search` 库命中不发请求 / 库空触发兜底（用 mock `NewsClient`）、`scope=mine` 只返回自选相关。
- 集成冒烟：`POST /api/v1/news/search {symbol:600519}` → 首次落库 → 第二次直接命中库（日志可验证未调 Python）。

---

## N4 前端搜索页 + 分析卡片（2–3 天）

> ⚠️ **执行顺序**：本节物理位置在 N5a 之前，但**实际执行在 N5a 之后**（N5a 是核心诉求且零新前端，优先级更高）。见 §0.1 与「工期与里程碑」。

**文件**
- 新建：`frontend/src/api/news.js`、`frontend/src/pages/News.vue`（可选 `frontend/src/components/NewsEventItem.vue`）。
- 修改：`frontend/src/router/index.js`（加 `/news`）、`frontend/src/pages/Dashboard.vue`（header 入口）、`frontend/vite.config.js`（**`/api/v1/news` 改 `NetworkOnly`**）。

**API 模块**（照 `api/messages.js` 的可选参数写法）

```js
export const searchNews = ({ symbol = '', keyword = '', types, days = 7, page = 0, size = 20 } = {}) => {
  const body = { symbol, keyword, days, page, size }
  if (types?.length) body.types = types
  return request.post('/news/search', body)
}
export const getStockEvents = (symbol, days = 30) => request.get('/news/events', { params: { symbol, days } })
export const analyzeEvents = (eventIds, force = false) => request.post('/news/analyze', { eventIds, force })
```

**页面结构（`News.vue`，照 `Strategies.vue` 骨架 + `Messages.vue` 分页）**
1. `<header>`：`← 返回` + 标题「资讯」。
2. 搜索区：股票输入（复用 `components/StockSearchInput.vue`，`v-model` + `ref`/`defineExpose` 先例见 Dashboard）+ 关键词输入 + 类型 pill（全部 / 公告 / 媒体 / 研报，照 `.pill/.pill.active`）+ 时间范围（照 `Alerts.vue`/`Messages.vue` 的 `timeRanges`）。
3. 结果列表：滚动到底加载（照抄 `Messages.vue` 的 `page/size/hasMore/loadingMore` + `main` 上 `@scroll` 阈值 100px）。
4. 事件卡片（照 `Messages.vue` 的 `.msg-card` 家族 + `typeMeta()` 图标映射模式）：
   - 左图标按 `source_level`（1公告📢 / 2媒体📰 / 3研报📊 / 4舆情💬）；
   - 标题（外链 `target="_blank"`）+ 时间 + 来源名 + 来源级别角标；
   - `plain_summary` 大白话摘要；`direction` 用色标（利好 `#ff4d4f` / 利空 `#52c41a` / 中性灰）；
   - `confidence` 低（如 <0.4）或 `direction` 为空 → 显示「信息不足，不判断方向」（**spec §6 的硬约束文案**）；
   - 行级「深度解读」按钮，用 `detailLoadingId` 行级 loading（照 `Strategies.vue` 的 `backtestLoadingId` 模式）。
5. 「深度解读」弹窗：照抄 `.modal-mask` + `.modal-box`（`@click.self` 关闭），内容 = `risks` 红标列表 + `opportunities` 绿标列表 + 免责声明。
6. 底部统一免责：「AI 生成内容，仅供参考，不构成投资建议」（spec §10）。

**验收**
- `npm run build` 无错；`/news` 可搜索、可分页、可打开解读弹窗。
- 弱网/接口挂掉 → 页面显示中文错误而不是白屏。
- 直连打开 `http://localhost:5173/news` 刷新不 404。
- **新公告不受 PWA 缓存影响**（NetworkOnly 生效）。

---

## N5a 盘前/盘后简报 Job（1–2 天）★ 核心诉求，零新前端

**文件**：新建 `job/NewsBriefJob.java`、`service/NewsBriefService.java`、`dto/NewsBriefRequest.java`；改 `frontend/src/pages/Messages.vue`（`typeMeta()` 加 `NEWS` 分支）。

**两个时间点**（照 `PaperTradingJob` 的写法，手写构造注入，**不用 `@RequiredArgsConstructor`**）：

```java
@Scheduled(cron = "0 30 8 * * MON-FRI", zone = "Asia/Shanghai")   // 盘前 08:30
public void runPreMarketBrief() { runQuietly("盘前简报", () -> briefService.buildAndPush(BriefType.PRE_MARKET)); }

@Scheduled(cron = "0 40 15 * * MON-FRI", zone = "Asia/Shanghai")  // 盘后 15:40（与 PaperTradingJob 的 0 30 15 错开 10 分钟）
public void runPostMarketBrief() { runQuietly("盘后简报", () -> briefService.buildAndPush(BriefType.POST_MARKET)); }
```

- **每用户独立事务**：`TransactionTemplate` + `PROPAGATION_REQUIRES_NEW`（照 `PaperTradingService`），循环体 `try/catch` 单用户隔离。
- **幂等**：`boolean existsByUserIdAndBriefTypeAndBriefDate(Long userId, String briefType, LocalDate briefDate)` —— **要区分盘前/盘后**，同一天两条都要能发。
- **推送**：`messageService.saveAndPush(user, "NEWS", content, symbol)`（唯一入口，内部含落库 + `withSymbolName` + SSE push）。⚠️ 同步改 `Messages.vue` 的 icon/label/tab，否则显示成"系统通知"。

**简报内容（保底版 = 模板拼装，不调 LLM 也能出）**

```
【盘前简报 · 09-14】
你关注的 12 只里，昨夜到现在有 3 条：
1) 🔴 立讯精密：重大合同（利好，置信 0.82）— 拿到 XX 客户 XX 订单，对全年营收影响约 X%
2) 🟡 贵州茅台：机构研报下调评级 —— 中银证券由"买入"调至"增持"
3) ⚪ 宁德时代：股东减持 1.2% —— 常规减持，历史上对股价影响有限

行业环境：白酒 — 近一月 11 篇研报，主流仍为"买入"，关注中秋动销
宏观：财新要闻 2 条（AI 算力投资、油价）
```
→ 每条都是 N2 已产出的结构化字段**拼装**而成。**这一步不需要 LLM**，这也是"保底版"的意义：即使 LLM 全线不可用，用户每天仍能收到一份可读清单。

**增强层（N5a 稳定后再开，默认关闭）**：`impact=high` 的事件交给现有 ReAct agent（`agent/react_agent.py`，`MAX_STEPS=6`）自主追查（查行情/历史/同业后写一段分析）；**失败/超时自动回退到保底模板**——绝不让用户"啥也收不到"。

**漏斗（写在这里，因为它服务的是简报与提醒）**：L1 代码规则（代码 ∈ 自选/持仓，或命中关键词表：减持/增持/处罚/立案/重组/业绩预告/回购/停牌/退市）→ L2 相关度打分 → L3 才做 LLM 抽取。**L1 绝不能交给 LLM**（成本乘 100 倍）。

**打扰分级（spec §1.2 反目标约束）**：`impact=high` 且 `direction≠中性` 且标的是自选/持仓 → 即时走 Alert；其余相关事件 → 攒进简报（一天最多 2 次）；全市场其余 → 只入库不推送。

**验收**：手动触发两个 Job → 消息中心各出现一条 `NEWS` 简报 → 同日重复触发不重复 → 把 LLM 关掉（`DEEPSEEK_API_KEY` 置空）仍能出保底简报。

---

## N5b 个股页事件区 + Dashboard 汇总卡（1–2 天，N5a 之后）

**文件**：修改 `frontend/src/pages/KLineChart.vue`、`frontend/src/pages/Dashboard.vue`。

- **N5b-1 归因对照条**（插在 `KLineChart.vue` 的 `.chart-container` **之前**）：
  「今日 +X%，期间相关事件 N 条（公告 x / 媒体 y）」+ 一句话归因（"股价已提前反应 / 与事件方向一致 / 无明显事件，大概率情绪波动"）。数据并入 `loadData()` 的并行请求，用 `.catch(() => null)` 软失败。
- **N5b-2 事件时间轴**（插在 `.price-info` **之后**、`</main>` 之前）：`getStockEvents(symbol, 30)`，每条 = 标题 + 摘要 + 方向色标 + 来源级别 + 时间，卡片样式沿用同文件 `.price-info` / `.indicator-panel` 规格。
- **N5b-3 Dashboard 汇总卡**（插在 `syncMsg` 与 `.search-section` **之间**）：复用 `.ths-card` 左色条样式，「今日与你自选相关的 N 条」默认折叠、点击展开；`onMounted` 里追加一行不 `await` 的 `loadNewsDigest()`，失败静默降级为 `[]`（照 `loadFavorites()` 模式）。
  - 数据来源 = N5a 已落库的简报（直接读 `messages` 里最新一条 `type=NEWS`，**不重复计算**）。

**验收**：个股页出现事件时间轴与归因对照条；Dashboard 汇总卡显示与最新简报一致的内容。

---

## N6 可选深挖（按需，不建议在 N1–N5 完成前开工）

> **N6-1「对话式问答」建议提前到 N4 之后立刻做**（半天量级），因为它是 §0.5 里唯一真正需要 Agent 的地方，且只需扩工具不改架构。

**N6-1 给现有 Agent 加 3 个新闻工具**（改 `python-data-service/agent/tool_registry.py`，不新建 agent、不新建端点）：

```python
_tool("search_news", "Search recent announcements, news and broker reports for a symbol or keyword. Use this whenever the user asks WHY a stock moved or what happened to it.",
      _obj({"symbol": {"type": "string"}, "keyword": {"type": "string"},
            "days": {"type": "integer", "default": 7}}, []))
_tool("get_stock_events", "Get the event timeline for one symbol (announcements/news/reports with direction and plain-language summary).",
      _symbol_props())
_tool("explain_event", "Explain one specific event in plain language: who it affects, how much, and for how long. Pass the event id returned by search_news.",
      _obj({"event_id": {"type": "string"}}, ["event_id"]))
```

- 工具描述里**必须写明"用于回答为什么涨跌"**——ReAct Agent 的工具选择完全依赖 description，这是它与"总调度 Agent"唯一真正等价的能力（选工具），而且只花一次 LLM 往返。
- `execute_tool` 里对应分支：`search_news` → 调 Java 搜索接口或直接复用 `news_client.search_news`；`explain_event` → 调 `news_understanding.analyze_one`。
- 现有 Agent 的 `MAX_STEPS = 6`、非法 JSON 重试一次、`split_replies` 分片全部复用，**零架构改动**。
- 验收：在聊天框问"茅台今天为什么跌"能自动查到事件并给出"与事件方向一致 / 无明显事件，大概率情绪波动"，且**不会**因此多出买/卖建议（沿用既有的禁投顾约束）。

| 项 | 内容 | 前置 |
|---|---|---|
| ~~N6-1~~ **对话式问答** | 扩 `tool_registry.py` 三个新闻工具（详见上） | **建议 N4 后立即做** |
| Chroma RAG 追问 | 单条新闻/公告页"问它"（spec Phase B）；`chromadb` venv 已装但**未进 requirements**，embedding 用智谱（`.env` 里的 `ZHIPU_API_KEY`，`zhipuai` 包**未安装**） | N6-1 + N2 质量达标 |
| 舆情情绪分 | 社区数据源 → 0–100 分 + 一句话总结（spec Phase C，P2） | 需另接数据源 |
| 研报盈利预测对比 | 用实测已有的 `2026/2027/2028-盈利预测-收益/市盈率` 做机构预期共识与修正方向 | N1 研报取数 |
| 公告知识图谱式解读 | 多公告对比、要素抽取（spec Phase B） | N2 稳定 |

---

## 关键风险（本模块特有）

| 风险 | 影响 | 缓解 |
|---|---|---|
| 🔴 **LLM 误判利好/利空** | 误导用户决策（比"没有功能"更糟） | `confidence` 门槛 + 低置信置空 + "影响方向≠涨跌预测"文案 + N2 的**人工抽查 10 条**验收 |
| 🟡 `stock_news_em` 只给 10 条 | 搜索"没结果" | N3 的「库优先 + 实时兜底」+ 每日增量落库累积（这是设计必需项，不是优化） |
| 🟡 全市场公告 469 条/日 | 存储膨胀 + LLM 成本 | 只对**自选相关**做 LLM 抽取；其余只存结构化字段（spec §13） |
| 🟡 akshare/东财接口改版 | 取数失败 | 字段映射集中一处 + 单源失败不阻断 + 巨潮备选 |
| 🟡 PWA 5 分钟 API 缓存 | 新公告不显示 | N4 给 `/api/v1/news` 加 `NetworkOnly` |
| 🟢 合规（若给他人用） | 备案/投顾红线 | `sanitize` 后置过滤 + 免责标识 + 预留"关闭 AI 解读"降级开关（spec §10） |

---

## 工期与里程碑

| 阶段 | 工期 | 里程碑（可演示） |
|---|---|---|
| N0 | 0.5 天 | `pytest` 全绿、工作区干净 |
| N1 | 1–2 天 | 命令行能按股票抓到公告/新闻/研报 |
| N2 | 1–2 天 | 一条真实公告 → 大白话解读 + 红绿风险机会 |
| N3 | 2–3 天 | `POST /api/v1/news/search` 能搜索且落库去重；漏斗 L1/L2 生效 |
| **N5a** | **1–2 天** | **★ 盘前/盘后自动收到自选相关简报（消息中心）—— 核心诉求达成，零新前端** |
| N4 | 2–3 天 | 页面能搜新闻、能看解读（想主动查时的入口） |
| N5b | 1–2 天 | 个股页有事件背景、Dashboard 有汇总卡 |
| N6-1 | 0.5 天 | 聊天框问"茅台今天为什么跌"能自动查事件并归因 |
| N6 其余 | 按需 | Chroma RAG 追问 / 舆情情绪分 / 研报预期对比 |
| **合计** | **约 10–15 人日** | **N5a 完成即达成"每天主动送达"的核心诉求** |

---

## 与 spec 的差异汇总（便于回填 spec）

1. spec §11 分期顺序调整：搜索/分析提到 P0，收盘汇总降到 N5（理由见 §0.1）。
2. spec §5 架构里的 `news_events` 表由 **Java 建表落库**（Python 无 DB 依赖），Python 只做取数与理解，不落盘。
3. spec §4 的 `stock_news_em` 只说"可用"，实测**每日仅 10 条** → 搜索策略必须"库优先 + 实时兜底"，spec 需要补这条。
4. spec §12 测试计划里的 Python 单测改回**单测 + 真实链路脚本双轨**（沿用 THS 的脚本验证传统，因为协议/接口问题只有真实链路能暴露）。
