# 休市提示 + 资讯首屏提速（设计）

> 2026-09-20 · 起因是用户的两条反馈：
> 1. **"明明是在休息日，却显示昨收，这里难道不应该提醒用户几天休息吗"**
> 2. **"本来新闻获取的就慢，用户点进来过了几秒才有新闻内容，结果出来以后还要再等一会才有你的 ai 分析"**

这两条看起来是两件事（一个是数据口径、一个是性能），但根因是同一种：
**页面在替用户猜。** 第一条是拿"今天"去标注一个属于上一个交易日的价格；
第二条是拿一个转圈去代替"还有多少没做完"。下面分别说清楚它们各自错在哪、
改了什么、以及哪些边界是刻意不猜的。

---

## 一、休市提示：行情到底属于哪一天

### 1.1 缺陷（实测）

`python-data-service/app.py` 的 `/api/v1/quote/{symbol}` 里有一行：

```python
today_str = str(date.today())
...
GlobalQuote(..., lastTradingDay=today_str, ...)
```

腾讯行情在休市日**照样返回数据**：`最新价` = 上一个交易日的收盘价、
`昨收` = 再上一个交易日的收盘价、`涨跌幅` = 上一个交易日当天的涨跌。
于是 2026-09-20（周日）打开首页会看到：

```
贵州茅台  600519
¥1400.00
+0.72%  昨收 1390.00                更新 2026-09-20
```

那个 `更新 2026-09-20` 是**编出来的**。价格是 09-18（周五）的，涨跌幅也是周五的，
而日期写的是今天 —— 用户看到的直接反应就是"休息日为什么还有今天的行情"。

**为什么前端救不了这件事**：行情响应里没有任何字段能说明"这个价是哪天的"。
`昨收` 只能说明"再上一天的收盘价"，推不出"今天是休市"。所以必须引入外部事实。

### 1.2 交易日历（新增 `python-data-service/trading_calendar.py`）

数据源：akshare 的 `tool_trade_date_hist_sina()`。选它而不是"排除周末 + 硬编码节假日"，
理由是**调休**：实测 2026-09-25（周五）休市（中秋调休）、
2026-10-01 ~ 10-07 休市、下一个交易日是 10-08（周四）。
任何"周末近似"都会把 09-25 说成开市，而那个错误的表现恰好就是本功能要消灭的那一个。

暴露为 `GET /api/v1/market/status`（Java 侧转成 `/api/v1/market/status`）：

| 字段 | 含义 |
|---|---|
| `tradingDay` | 今天是否交易日。**日历不可用时是 `null`（不知道），不是 `false`** |
| `phase` / `phaseLabel` | pre_open / auction / morning / noon_break / afternoon / post_close / closed |
| `quoteDate` | **行情里的"最新价"属于哪一个交易日** —— 本次修复的核心字段 |
| `lastTradingDay`（原 `lastTradingDay`） | 现在由 `quoteDate` 提供，不再是 `date.today()` |
| `nextTradingDay` / `restDays` / `restFrom` / `restTo` | "休到哪天""连休几天" |
| `known` / `calendarSource` | 日历是否覆盖今天、数据来自 akshare 还是磁盘缓存 |
| `note` | 已经拼好的中文句子，前端直接渲染 |

**`quoteDate` 的三条规则**（每条都有对应用例）：

| 场景 | tradingDay | quoteDate |
|---|---|---|
| 交易日 09:15 之后 | true | 今天 |
| 交易日 09:15 之前（未开盘） | true | 上一个交易日 |
| 休市日 | false | 上一个交易日 |

第一行与第二行必须**分开**：`tradingDay=true` 但 `quoteDate=上一交易日` 是一种正常状态。
只有一个字段的话，前端只有两种错法 —— 要么把周五的价说成今天的，
要么把今天说成休市。

### 1.3 诚实边界：不知道就说不知道

- 日历**不覆盖今天**（每年 12 月前后新浪才放出下一年安排）→ `known=false`，
  按"工作日近似"给一个下限答案，并在 `note` 里写明"未覆盖 / 按工作日近似"。
  与 `PaperSchedule` 的既定口径一致。
- 日历**彻底拿不到**（上游挂 + 无磁盘缓存）→ `tradingDay=null`，`note` 说明原因。
  Java 侧 `MarketStatusService.unknown()` 也一样给 `null` ——
  给 `false` 会让界面显示"今日休市"（虚惊），给 `true` 会把休市价当成实时价（正是本 bug）。
- 降级顺序：内存缓存（12h）→ akshare（成功即落盘）→ 磁盘缓存 → 空。
  **任何一步失败都不抛异常**：把"我不知道今天开不开市"升级成"你看不到行情"是更糟的结果。

### 1.4 前端

- `composables/useMarketStatus.js`：**模块级单例**（首页与个股页共用；60 秒 TTL 与后端对齐）。
- `utils/marketStatus.js`：全部"状态 → 文案"的判断，**纯函数、不依赖 Vue**，
  末尾带可直接用 Node 跑的用例（`verifyMarketStatusWording()`）。
  抽出来的理由与 `utils/newsAnchor.js` 相同：这些判断错了肉眼看不出来，
  而它们只在**边界日**才露出来（调休、长假、开盘前），一年遇不到几次。
- 显示：首页与个股页顶部一条 `.market-notice`（在 `style.css` 里，两处共用同一份样式）：
  > `[休市] 今日周末休市（周日） · 本轮连休 2 天（09-19 ~ 09-20） · 下一交易日 09-21（周一，明天）`
  > `行情为 09-18（周五）收盘价，非实时`

  句子用后端的 `note`，前端**只**决定版式 + 补一句"页面上这些价格是哪天的"
  （这一句后端说不了，它不知道页面上在显示哪些价）。两侧各拼一半，
  就是"同一件事两种说法"的开始。
- 行情卡的 `更新 <日期>` 改写成 `最近交易日 09-18（周五）收盘`；
  K 线图副标题同理，并在未开盘/午间休市/已收盘时补一个状态前缀。

---

## 二、资讯首屏：先给内容，再长解读

### 2.1 缺陷

`NewsService.timeline` 在返回**之前**同步做完三件事：

1. 读库；
2. 若库过期 → 抓上游（4 个外部 HTTP，全市场公告上千行）；
3. 给最多 30 条未解读的条目补解读 → Python 侧 `BATCH_SIZE = 5`，**串行 6 次** LLM 调用。

于是用户点进个股页先看到一行"资讯加载中…"，几秒后才有列表。

更糟的是第 3 步**并不总是能成功**：`NewsClient.REQUEST_TIMEOUT = 30s`，
而 6 次串行 LLM 往返很容易超过它 —— 超时后 `analyzeAndPersist` 记一条 warning 返回 0，
**整批解读一起丢**。用户白等 30 秒，一条解读都没有。
（这一条此前没有任何用例覆盖，因为"会不会超时"取决于模型快慢。）

### 2.2 改法

`GET /news/events` 改成"**立刻返回库内内容 + 后台补数据 + 报告进度**"：

```
StockNewsTimelineResponse(events, analyzing, analyzedCount, total, pendingCount)
```

- `timeline()` 只做：读库 → 判断"要不要补" → `scheduleEnrichment()` → 立刻返回。
- 后台任务（`newsEnrichmentExecutor`，4 线程，**与取数池隔离**）：
  需要则抓上游 → 然后**分批**（`ANALYZE_BATCH_SIZE = 5`）解读，每批独立落库。
  - 批大小从"30 条一次"改成 5 条一次：单次 HTTP 调用有界（不再撞 30 秒超时），
    且每批落库后**立刻对下一次轮询可见** —— 解读是"长出来"的，不是一次全亮。
  - 顺序按时间倒序取（`newestFirst()`）：先补最新的一批，
    因为"最近发生的事"才是用户点进来的原因。
- **单飞**：`enrichments: Map<String, CompletableFuture<Void>>` 保证同一标的只有一个在途任务。
  用 `putIfAbsent` 先占位再提交，而不是 `computeIfAbsent` —— 后者在"执行器同步执行"时
  会让任务先跑完、再从映射里删掉，然后才把已完成的 future 放进去，
  留下一条永不消失的"在途"记录（用例里就是同步执行器）。
- `analyzing` 取自"**当前是否有在途任务**"，不是"这次有没有发起"：
  并发请求里只有一个能拿到冷却授权，其余几个按"我没发起"回答就会告诉前端
  "没有在解读" → 前端停止轮询 → 用户看到一份永远补不全的列表。

### 2.3 「当前怎么看」：并行 + 有界等待 + 不缓存半成品

- 前端**并行**发起 `/news/events` 与 `/news/stock-read`（原来是等事件就绪才发，
  等于把两次等待串起来）。
- `stockRead` 内部 `awaitUsableAnalysis(key, 10s)`：**边等边看库里有没有解读**，
  一有就立刻拿去合成。
- 超时**不是失败**：用已有解读照常生成，但**这一轮不写缓存**。
  缓存一段基于半批数据的判断，会让用户在列表早已补全的情况下反复看到它 ——
  那比再花一次模型调用更不值得。
- 前端：骨架屏（不冒充内容）+ 轮询（2 秒一次，`setTimeout` 递归而非 `setInterval`，
  避免慢的时候堆并发让"已解读 x/y"来回跳）+ 上限 40 轮
  （**兜底而非预期**：后端说还在解读但解读永远不会来时，前端必须自己收手）。

#### 2.3.1 真机联调推翻的第一版（重要）

第一版是"等 future 结束，最多 8 秒"。跑起来才发现它错得离谱：

| 标的 | 库里状况 | 后台补数据耗时 | 第一版 `/stock-read` |
|---|---|---|---|
| 600519 | 23 条全已解读 | 无需补 | 63 ms（列表）/ 正常出结论 |
| 000001 | 8 条、0 条已解读 | 11.0 s | **10 s 超时 → `null`** |

根因：`future` 只在**全部 30 条**读完时才完成，而冷标的上"抓上游 + 第一批解读"
就要 8~15 秒（实测 `002594` 14.7 s、`601318` 12.8 s）。于是必然超时，
而超时那一刻库里可能**一条解读都没有** → 返回 `null` → 用户看到一份完整列表、
却**永远**没有结论（前端只问了一次）。

两处修法，缺一不可：

1. **后端：等"可用"，不等"完整"**。`awaitUsableAnalysis` 每 400 ms 看一眼库里
   有没有解读，一有就先给结论。综合解读真正需要的是"**读没读过**"，
   不是"读完了没有" —— 5 条读过 vs 30 条读过的差别，远小于"有结论 vs 没结论"。
2. **前端：第一次空手而归不算数**。`maybeRetryStockRead` 在轮询报出
   "有解读了 / 解读结束了"之后**补问一次**（只补一次，避免后端确实给不出结论时反复花钱）。

修完后的真机实测：

| 标的 | 列表首屏 | 结论 | 备注 |
|---|---|---|---|
| 600519（热） | 63 ms | 首问即得 | `analyzing=false` 后第 2 次轮询就停 |
| 000001（半冷） | 39 ms（3 条） | **2.2 s** | 日志：`综合解读先取用已就绪的解读` |
| 002594（全冷） | 32 ms（0 条，`analyzing=true`） | 10 s 超时 `null` → 补问 **2.7 s 出结论** | 日志：`等待后台补数据超过 10 秒` |

10 秒这个上限**刻意不再往上加**：冷标的补数据要 11~15 秒，把上限提到 15 秒
只是让一个 Tomcat 线程白等更久，用户在那段时间里看到的仍然是同一个转圈；
而"先返回 + 前端补问"让用户在这 15 秒里能看到列表与逐条解读在长出来。


### 2.4 一条容易被忽略的诚实性改动

后台补解读期间，列表里会有很多条"还没有解读"。原来它们统一渲染成
**"信息不足，不判断方向"** —— 而 spec §6 明确区分"模型读过但判不出方向"与"还没轮到它"。
本轮改造让这一瞬间同时出现大量未解读卡片，混为一谈等于把"没问"说成"问过了"，
用户会直接得出"AI 读不出东西"的结论。所以未解读且 `analyzing=true` 时改说
**"正在解读这条…"**。

---

## 三、改动清单

**Python**
- 新增 `trading_calendar.py`、`tests/test_trading_calendar.py`、`tests/test_market_status_endpoint.py`
- `app.py`：新增 `/api/v1/market/status`；`quote` 的 `lastTradingDay` 改用 `quoteDate`；lifespan 预热日历
- `.gitignore`：`market_calendar_cache.json`

**Java**
- 新增 `MarketStatusResponse`、`MarketController`、`MarketStatusService`、`MarketStatusServiceTest`
- 新增 `StockNewsTimelineResponse`
- `AkshareStockClient`：`getMarketStatus()`；`emptyQuote` 的日期改为 `null`（原来是 `LocalDate.now()`，同一个谎）
- `NewsService`：`timeline` 改签名与行为、`scheduleEnrichment` / `enrich` / `awaitEnrichment`、
  分批 `analyzePending`、`stockRead` 有界等待 + 部分数据不缓存
- `AsyncConfig`：新增 `newsEnrichmentExecutor`
- `NewsServiceTest`：同步执行器 + 两个新用例（接口不被拖住、半成品不缓存）

**前端**
- 新增 `api/market.js`、`composables/useMarketStatus.js`、`utils/marketStatus.js`
- `api/news.js`：返回形状改成信封 + `prefetchStockNews`
- `Dashboard.vue`：休市提示条、`quoteDateText`、进个股页前预取资讯
- `KLineChart.vue`：休市提示条、副标题口径、骨架屏、轮询、进度文案、
  未解读文案分叉、`chartSubtext` 用 watch 单独更新（不重画整图，避免重置 dataZoom）
- `style.css`：`.market-notice`（两页共用）

---

## 四、仍然存在的缺口（不假装已解决）

1. **交易日历覆盖期**：新浪只公布到当年年末。跨年那几天会退到
   "工作日近似 + 说清楚是近似"，不会给出错误答案，但也不能给出准确的长假天数。
2. **后台补解读没有进度推送**：靠前端轮询（2 秒）。用现有 SSE（`messageBus`）推
   "解读完成"事件会更省，但本轮没做 —— 轮询的请求量是每票每 2 秒一次，可接受。
3. **`ANALYZE_BATCH_SIZE` 与 Python 的 `BATCH_SIZE` 是两处常量**：不一致不会报错，
   只会变慢（一次 HTTP 里塞两批）或变碎（一批拆两次）。没有测试能跨语言钉住它。
4. **前端没有自动化测试**：`verifyMarketStatusWording()` 只能手动跑
   （`node --input-type=module -e "import('./src/utils/marketStatus.js')..."`），
   没有接进任何 CI。也就是说 §2.4 那条"补问一次"的逻辑**没有用例保护**。
5. **冷标的首访仍是"列表先到、结论十几秒后到"**：这是模型往返的物理下限，
   不是可以靠改代码消掉的。真正的解法是**在用户点进个股页之前就开始补数据**
   （首页已按这个思路加了 `prefetchStockNews`，但它只在"点击自选/搜索结果"那一刻触发，
   覆盖不到"直接输代码进页"和"冷启动后第一次访问"）。

