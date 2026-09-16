# 新闻/资讯模块设计文档 (News & Events Module Design)

> 状态：草案待评审 · 日期：2026-09-05 · 场景：个人自用起步，预留"给其他人用"的合规边界
> 关联：`2026-09-05-ths-watchlist-sync-design.md`（自选/持仓数据为其上游）；README 路线图中的"RAG 接入研报/新闻情感"为此文档的落点。

## 1. 背景与目标

### 1.1 要解决的用户问题

散户与机构的信息差不在"有没有信息"，而在三层差：**时间差、解读差、噪音过滤差**。用户面对一条新闻真正要回答的是：**"它对我持有/想买的股票意味着什么，我要不要做点什么？"** 本项目新闻模块的使命：**过滤 + 归因 + 翻译**，而非提供更多资讯。

### 1.2 反目标（不做什么）

- 不做 24 小时资讯轰炸 / 瀑布流信息流（对散户价值低甚至有害）。
- 不做"新闻 → 涨跌预测"式承诺（无法兑现，且触碰投顾红线）。
- 不把自媒体/社区帖子与公告混为同权重的"资讯流"。

## 2. 竞品调研结论（怎么实现）

| 产品 | 形态 | 实现要点 |
|---|---|---|
| **moomoo / 富途 Agent Hub**（[GitHub](https://github.com/MoomooOpen/moomoo-agent-hub)） | 资讯搜索 / 个股解读 / 情绪温度计，拆成标准化 Skill，MCP 兼容 | ① 新闻/公告/研报**分类型**搜索（`type: 1新闻/2公告/3研报`）；② 排序维度 **PV/时间/热度**；③ **情绪用 0–100 标准化分数**而非帖子列表；④ 内容接口公开 HTTP、响应标注非投资建议 |
| **新浪财经 喜娜 AI**（[证券日报](http://www.zqrb.cn/tmt/tmthangye/2025-03-17/A1742182413518.html)） | 个股公告 AI 解读 | DeepSeek 专项训练 + **金融知识图谱**：识别财务指标/风险提示/重大合同 → **结构化报告，红标风险、绿标机会**；规划多公告对比 |
| **雪球 全球事件库**（[雪球](https://xueqiu.com/1474197611/360127664)） | 个股事件时间轴 | 新闻/公告/大事整理成**时间轴**，为技术分析补事件背景——归因查询的底座 |
| **FinChat.io**（[介绍](https://moge.ai/zh/product/finchatio)）/ Benzinga（[API+GPT 摘要](https://www.benzinga.com/apis/blog/openai-benzinga-news-summarization/)） | 对话式投研 / 摘要 API | "结构化数据 + LLM 解读"工业化路线 |

**共性范式（本设计直接采用）**
1. 信源**分开建模、分级存储**：公告 > 财经媒体 > 研报 > 社区舆情，权重与建模各自独立；
2. AI 输出**结构化字段**（事件类型/影响方向/涉及代码/置信度/来源级别），不是每篇写作文；
3. 舆情呈现为**情绪分数**，LLM 总结仅作角落补充；
4. 展示 = 个股事件时间轴 + 收盘"与你相关"汇总 + "新闻 vs 价格是否已反应"对照。

## 3. 产品形态（A/B/C/D 组合）

| 形态 | 说明 | 优先级 |
|---|---|---|
| **A. 事件雷达（被动）** | 只推**自选/持仓**相关的公告与重大新闻；交易日收盘后一次汇总；重要突发事件走现有预警推送 | P0 |
| **B. 归因查询（主动）** | 个股页/持仓页回答"今天/近期为什么涨跌"：展示事件时间轴 + 当日涨跌幅与事件的对照 | P0 |
| **C. 单条深度解读** | 对重要公告/新闻做 LLM 大白话拆解：利好谁/影响多大/持续多久（新浪红绿标注模式） | P1 |
| **D. 信息流（角落）** | 首页"今日要闻"轻量流（默认折叠，非主形态） | P2 |
| **舆情温度计（角落）** | 社区舆情 → 0–100 分数 + LLM 一句话总结 | P2 |

## 4. 信源分级与数据源可行性（已实测）

项目 Python 环境 akshare 1.18.64，以下接口存在且可用：

| 级别 | 信源 | akshare 函数（实测存在） | 返回关键字段（实测） |
|---|---|---|---|
| L1 公告 | 东财公告（同步自巨潮） | `stock_notice_report(symbol='全部', date=...)` | 代码、名称、标题、**公告类型**（董事会公告/高管变动/…）、日期、链接 |
| L1 公告 | 巨潮披露 | `stock_zh_a_disclosure_report_cninfo(...)` | （备选，更全但更慢） |
| L2 媒体 | 东财个股新闻 | `stock_news_em(symbol='600519')` | 关键词、标题、内容、发布时间、**文章来源**、链接 |
| L2 媒体 | 财新/要闻 | `stock_news_main_cx` / `news_cctv`（新闻联播） | 宏观/政策信号 |
| L3 研报 | 东财研报 | `stock_research_report_em(...)` | 研报标题/机构/评级 |
| L4 舆情 | 社区（股吧/雪球） | 需另接（如爬虫/第三方）；本阶段可选 | 讨论热度/观点 |

> 说明：公告按"date + symbol=全部"每日拉取全市场增量，存库后按代码筛选——避免对自选逐只轮询被打断。实测拉取约 1–2s（13 只进度条），可接受为每日定时任务。

## 5. 总体架构

```
① 数据管道（Python 定时任务 + 手动触发）
   公告 stock_notice_report / 巨潮  → L1
   媒体 stock_news_em / 财新       → L2
   研报（可选）                    → L3
   舆情（可选）→ 情绪分            → L4
   ↓ 统一落表：news_events(id, symbol, title, summary, url, source_level,
        event_type, direction, confidence, related_symbols, published_at)
② 理解层（LLM，Python llm_service）
   新入库事件批量抽取 → 结构化字段（schema 见 §6）
   公告深度解读（P1）：知识图谱式要素抽取 + 红/绿标注
   舆情 → 情绪分数 + 一句话总结（P2）
   ↓ 原文向量化 → Chroma（RAG 召回/问答）
③ 存储
   MySQL/H2: news_events + news_stock_rel（事件↔代码多对多）
   Chroma: 向量（README 已规划 RAG 落点）
④ 服务与展示
   Java: NewsService（按用户自选/持仓过滤）+ 收盘汇总 Job + 预警联动
   前端: 个股详情页"事件/解读"区 + Dashboard 收盘汇总卡
```

**Java ↔ Python 边界**：沿用现有模式——Python 拥有数据与 AI 理解（`news_client.py` + `llm_service`），Java 拥有用户数据、权限、聚合与触达（自选关联、预警、汇总 Job）。

## 6. AI 结构化字段 Schema（理解层唯一契约）

```json
{
  "event_id": "string",
  "event_type": "业绩预告|重大合同|回购|增减持|监管处罚|重组|宏观政策|行业动态|其他",
  "direction": "利好|利空|中性",          // 影响方向（非涨跌预测）
  "confidence": 0.0,                       // 0~1
  "impact_level": "high|medium|low",       // 影响强度：是否可能改变中期逻辑
  "related_symbols": ["SH600519"],         // 归一化代码（复用 THS 文档 §4 规则）
  "source_level": 1,                       // 1公告 2媒体 3研报 4舆情
  "plain_summary": "大白话一句话：发生了什么 + 对持有者意味着什么",
  "risks": ["..."],                        // 红标风险点（P1 公告解读）
  "opportunities": ["..."]                 // 绿标机会点（P1 公告解读）
}
```

约束：`direction` 是"影响方向"而非"明日涨跌"；`confidence` 低或信息不足时允许置空并显示"信息不足，不判断方向"。

## 7. 数据模型变更

```sql
-- 事件主表（Python 与 Java 共享的事实层）
news_events(
  id BIGINT PK, symbol VARCHAR, title VARCHAR, summary TEXT,
  url VARCHAR, source_level TINYINT, source_name VARCHAR,
  event_type VARCHAR, direction VARCHAR, confidence DOUBLE,
  plain_summary TEXT, risks TEXT, opportunities TEXT,
  published_at DATETIME, created_at DATETIME
)
-- 事件↔代码（一条宏观新闻关联多股）
news_stock_rel(id PK, event_id, symbol, INDEX(symbol, published_at))
```

- 展示"与用户相关"：`JOIN favorite_stocks/positions (user_id)` ON symbol——**过滤条件天然来自自选/持仓**。
- 去重键：`(url)`；公告用 `(symbol, title, published_at)` 兜底。

## 8. 展示与触达设计

- **个股详情页**：新增"事件与解读"区——时间轴（公告/新闻按时间），每条 = 标题 + `plain_summary` + 方向色标 + 来源级别 + 时间；点"深度解读"进 C 形态报告（红/绿标注）。
- **归因对照条**（B）：个股页顶部——"今日 +X%，期间相关事件 N 条（公告 x / 媒体 y）"，再给一句话："股价已提前反应 / 与事件方向一致 / 无明显事件，大概率情绪波动"。
- **Dashboard 收盘汇总卡**（A）：每交易日收盘后一次"今日与你自选相关的 N 条"，点击展开，绝不打扰式推送；重大突发事件（impact=high 且 direction≠中性）走现有 Alert 推送通道。
- **社区舆情（P2，角落）**：个股页角落温度计 0–100 + LLM 一句总结，不带入主决策区。

## 9. 与现有代码的集成点

| 层 | 改动 | 复用 |
|---|---|---|
| Python 新增 | `news_client.py`（akshare 封装、分级拉取、去重）；`news_understanding.py`（LLM 结构化抽取）；`app.py` 加 `/api/v1/news/events`、`/api/v1/news/analyze`、`/api/v1/news/sentiment` | `llm_service`、`akshare_client` 模式、`x-internal-token` |
| Java 新增 | `entity/NewsEvent`（镜像表）、`service/NewsService`（按 user 过滤/聚合）、`job/NewsDigestJob`（收盘汇总）、`controller/NewsController` | `favorite_stocks`、Alert 推送、Job 调度先例（`StockPriceRefreshJob`）、WebClient 模式 |
| 前端新增 | 个股页事件区、Dashboard 汇总卡、Profile 开关（是否推送/汇总时段） | 现有页面结构 |
| 数据库 | 两张新表 + 每日定时拉取任务 | MySQL/H2 自动建表（JPA） |

## 10. 合规边界（"可能给其他人用"必须预留）

- **生成式 AI 备案**：给公众提供 AI 生成内容服务需完成生成式 AI 服务登记（新浪喜娜即为此备案）；文档与代码预留"内容审核 / 免责声明 / 关闭 AI 解读的降级模式"。
- **不构成投资建议**：所有 AI 输出带免责标识；`direction` 定义为"影响方向"；不出现"建议买入/卖出"句式（可在 prompt 与后置过滤双重约束）。
- **信源版权**：公告属公开披露可转述；媒体新闻以"标题+摘要+原文链接"形态引用，不全文搬运。
- 个人自用阶段无上述硬约束，但实现上保持一致，避免将来返工。

## 11. 分期与任务清单

### Phase A（P0，先做：公告/媒体管道 + 时间轴 + 收盘汇总 + 对照条）
- [ ] Python：`news_client.py`——公告/媒体定时拉取、分级存储、去重、`normalize_symbol` 复用
- [ ] Python：`news_understanding.py`——LLM 结构化抽取（§6 字段），失败降级为"仅存原文+方向空"
- [ ] Python：`app.py` 路由 `/api/v1/news/events`、`/api/v1/news/analyze`
- [ ] Java：`NewsEvent` 实体/仓库 + `NewsService`（按用户自选过滤）+ `NewsController`
- [ ] Java：`NewsDigestJob` 收盘汇总 + 重大事件走 Alert
- [ ] 前端：个股页事件时间轴 + 归因对照条；Dashboard 收盘汇总卡
- [ ] 端到端冒烟 + 免责文案

### Phase B（P1：深度解读）
- [ ] 公告知识图谱式解读（红/绿标注、风险/机会抽取、多公告对比）
- [ ] 单条新闻页"问它"（基于 Chroma RAG 的追问）

### Phase C（P2：舆情角落 + 信息流）
- [ ] 社区数据源接入 → 情绪分数 + LLM 一句话总结
- [ ] 首页轻量"今日要闻"（默认折叠）

## 12. 测试计划

- Python：`tests/test_news_client.py`（mock akshare 返回；分级/去重/字段映射）；`tests/test_news_understanding.py`（LLM mock 的结构化 schema 校验、失败降级）。
- Java：`NewsServiceTest`（用户自选过滤、去重、汇总拼装）；`NewsDigestJobTest`（幂等）。
- 手动冒烟：拉真实公告 → 详情页时间轴出现 → LLM 解读合理 → 收盘汇总只含自选相关。

## 13. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| akshare/东财接口变更 | 拉取失败 | 封装集中、错误可读、手动重试；公告走巨潮备选 |
| LLM 抽取幻觉（误判利好利空） | 误导 | `confidence`/置空机制 + "影响方向≠预测"文案 + 低置信降级 |
| 事件噪音（全市场公告量大） | 无关推送 | 只对自选/持仓股票代码过滤 + impact_level 门槛 |
| AI 输出合规（若给他人用） | 合规风险 | §10：备案预留、免责、禁止句式后置过滤 |
| 存储膨胀 | 磁盘/查询慢 | 只留结构化字段 + 原文剪裁；历史事件滚动清理 |
