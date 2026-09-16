# 记忆系统设计（账本 → 前情提要 → 事实 → 经验 → 混合检索）

日期：2026-09-16
状态：M0 / M1 / M2 已实现并通过全量测试
相关：`2026-09-02-strategy-agent-design.md`（策略 Agent）、`docs/superpowers/plans/`

## 1. 背景与目标

改造前的事实（这些都是用户能直接感知的问题，不是理论缺陷）：

- `app.py` 调 `run_agent(user_id, message)` **不传 history**，Java 的 `ChatService.callLlm` 也只发
  `user_id` + `message`，`run_agent` 的 `user_id` 参数从未被使用（注释自己写着"是个装饰"）——
  于是**每条消息都交给一个全新、失忆的 agent**："它呢？""把止损改成 5%" 必然失效。
- `SYSTEM_PROMPT` 里**没有任何当前时间**，模型无从把"去年""上周"锚定到绝对日期。
- 所有领域知识塞在一个 6KB 的 prompt 里；工具返回格式各不相同（见下）。
- 没有任何跨会话记忆：换天即失忆，用户不得不重复自己。

目标：**让 agent 少问一遍**，同时把"记住什么、什么时候记、谁能改"变成可解释、可审计、
不越权的工程约束。

非目标（明确不做）：
- 不做"记住一切"：实时行情、可随时重查且会变的数据不进长期记忆。
- 不做 agent 主动写记忆（`memory_note`）：写入统一走离线巩固流水线，见 §7。
- 不在 M1/M2 做物理删除：用户删除权是合规动作，单独通道（§7、§9）。

## 2. 分层与职责

| 层 | 存什么 | 谁写 | 生命周期 |
|---|---|---|---|
| L0 账本 `memory_events` | 每一轮 user/assistant 消息、策略生成、**工具调用结果** | Java（消息/回复/策略）+ Python（工具结果，批量） | 只追加；90 天后可归档降采样（未实现） |
| L1 情节 `memory_episodes` | 会话"前情提要"摘要，**版本化** | Python 生成 → Java 落库 | 派生数据，可重放重建 |
| L2 事实 `memory_facts` | 偏好/约束/持仓/目标/决定，带有效期与取代链 | 同上 | 派生；改口 = 追加新事实 |
| L3 经验 `memory_lessons` | 症状 → 尝试 → 解法 → 可复用做法 | 同上 | 派生；默认 pending，需人工确认 |
| 派生索引 | Chroma/向量（**当前用进程内 numpy**）、检索语料 TTL 缓存 | Python | 可丢可重建 |

三条流水线：

```
写入:  捕获 → 分类 → 落账(append)
巩固:  触发(跨天/节奏) → 摘要+抽事实+抽经验 → 落库 → 索引失效     [异步, 不在关键路径]
检索:  意图(query/symbol) → Java 关键词+类型+时间排序 ┐
                                                    ├→ RRF 融合 → 预算裁剪 → 注入 <memory>
       本地语义召回(向量, 可缺失)                     ┘
```

## 3. 核心不变量（改代码前必须先看这一节）

1. **内容只追加**。纠正/改口 = 追加新记录，不是 UPDATE。
   唯一允许就地更新的字段是 `MemoryLesson.status`（审核元数据，不是记忆内容）。
2. **事实失效由取代链推导**，不落 `valid_to`：
   "当前有效的事实 = 没有任何行指向它"（`NOT EXISTS` 子查询）。这样"只追加"与"会失效"同时成立。
3. **撤回不删除**：`retract` 追加一条 `factType=retraction` 的行指向目标，
   目标因此失去"当前有效"资格，历史链完整保留。（物理删除见 §9。）
4. **三个时间**：`eventTime`（内容所指，"去年"）、`validFrom`（开始成立）、
   `recordedAt`（我们何时记下）；原始措辞 `rawTimePhrase` 必须保留。
   相对时间**在写入时**解析成绝对区间（见 `agent/timeutil.py`），**解析不出来就不带时间，不猜**。
5. **agent 碰不到数据层**：Python 无数据库凭据，只能通过
   `/api/v1/internal/memory/*`（共享密钥）读上下文/读事件/追加事件/写摘要事实经验。
   接口形状本身就限制了能力——**没有 update/delete 可调用**。
6. **失败无害（fail-open）**：记忆、embedding、巩固、账本任何一环出问题，
   都只能退化成"这次没有记忆"，绝不能影响用户这条消息的回复。
   唯一 fail-closed 的地方是内部接口鉴权（未配置密钥时一律拒绝）。
7. **记忆是数据不是指令**：注入块开头明确声明；`provenance`/`trust` 区分
   用户说的（high）与模型产出的（medium）；工具输出一律 low。
8. **有预算**：工具结果 ≤4000 字符、记忆块 ≤2600 字符、事实单行 ≤140 字符、
   注入事实 ≤8 条、经验 ≤3 条。

## 4. 时间与"改口"如何生效

```
用户："帮我做均线策略，止损 8%"        → fact#41 stop_loss_pct=8   supersedesId=null
用户："我改主意了，止损改成 5%"        → fact#57 stop_loss_pct=5   supersedesId=41
                                            ↑ #41 从此不再"当前有效"
注入给模型：- 止损：5（2026-09-16；此前为 -8）
```

- 判定在 Java 侧（同键 + 值不同 = 取代）：是一致性问题，不能交给 agent 判断。
- **键必须稳定**：`agent/facts.py` 用别名表把"止损"与 `stop_loss_pct` 收敛成同一个键——
  否则取代链断掉，系统里会同时存在两个互相矛盾的止损值。
- "去年"这类时间在写入时解析成 `2025-01-01T00:00:00`，
  用户看到的解释是"你当时说的是'去年'"。

## 5. 巩固（consolidation）：什么时候记

| 触发 | 条件 | 目的 |
|---|---|---|
| 跨天 | 用户进入新会话（会话键 = `userId:yyyy-MM-dd`） | 把上一段对话补成前情提要 |
| 会话内节奏 | 累计事件 ≥ 2 → 4 → 之后 `8 × version` | 当天改口不必等到第二天 |
| 冷却 | 同会话 60s 内只触发一次 | 异步还没写完时避免重复烧 LLM |

**触发点用绝对阈值而不是取模**，这一点是踩过坑的：事件是"用户消息先落账 → 助手回复后落账"，
而检查发生在处理用户消息时，所以**看到的计数永远是奇数**（1、3、5、7…）。
写成 `count % 8 == 0` 或 `count == 2` 在生产里永远不会命中，而单测里手写偶数反而会通过。

前情提要的**兜底**：摘要异步生成，用户隔几天回来问"上次那个策略"时第一条消息常常还没摘要——
所以 `/context` 会额外带一份最新未总结会话的**原文摘录**（不花 LLM 调用）。

## 6. 检索与注入

- **Java 侧**：关键词（ASCII 词 + 中文 2-gram）+ 类型优先级（偏好/约束 > 观察）+
  用户确认过 + 弱新鲜度（180 天线性衰减，刻意做弱：事实不是"越新越对"）。
- **Python 侧**：向量语义召回（embedding 走 OpenAI 兼容接口，见 `.env.example` 的
  `EMBEDDING_API_KEY` / `EMBEDDING_MODEL`），**RRF 融合**（k=60，只用名次不用分数）。
  向量不可用时自动退化成纯关键词排序。
- **注入**：`<memory today=... tz=...>` 块，依次是
  长期画像 → 相关事实（含变更链）→ 经验 → 语义召回的历史对话 → 上一段对话原文摘录 →
  更早的对话摘要 → 缺口提示。

### 6.1 向量存在哪里（用不用向量数据库）

`VECTOR_BACKEND` 二选一，**默认 `numpy`**：

| 后端 | 形态 | 何时用 |
|---|---|---|
| `numpy`（默认） | 进程内暴力余弦，TTL 300s，不落盘 | 单用户语料几十到几百条：亚毫秒级，且**不存在"索引与事实源不一致"** |
| `chroma` | 落盘 PersistentClient，每用户一个 collection | 多实例/多 worker、语料涨到几千条、需要元数据过滤或增量更新 |

切换只改一个环境变量；Chroma 初始化失败会自动退回 numpy（有测试钉住）。

三条设计约束：

1. **向量全部由我们自己算**（智谱/OpenAI 兼容接口 + 本地 jsonl 缓存），
   collection 以 `embedding_function=None` 创建 —— 换 embedding 供应商不需要动索引代码，
   也不会出现"索引库偷偷调了别的模型"这类难查的问题。
2. **索引是派生数据**：失效时重新从 Java 拉语料，upsert 新条目并**删除已不在语料里的旧 id**
   （事实被取代/撤回后不能继续被召回）。这一步由测试覆盖。
3. **MySQL 8 没有原生向量能力**（向量类型/索引在 9.x/HeatWave 才有），
   所以"把向量直接放进业务库"这条路在当前版本下不可行 —— 要么进程内，要么独立索引库。
   这一点也是选 numpy/Chroma 而不是"扩展 MySQL"的原因。

> 为什么不默认上向量库：多一个常驻依赖、多一份要保证一致性的状态，
> 而当前规模下它带来的收益是零。触发条件（多实例、几千条、跨用户检索、冷启动延迟）
> 一旦出现，改一个环境变量即可。
- 为什么用 numpy 而不是向量库：单用户语料是几十到几百条，暴力余弦更快更简单，
  也没有"索引与事实源不一致"的问题。`chromadb` 已装但暂未启用；
  换成 Chroma 只需替换 `VectorIndex` 的 build/search 两个方法。
- embedding **不发送 `dimensions`**：bge-m3 这类模型不支持 Matryoshka 截断，带了会 400。

## 7. 安全与信任模型

| 风险 | 措施 |
|---|---|
| 记忆投毒（外部内容诱导持久化伪造记忆） | 外部/工具内容 `trust=low`；事实与经验一律 `provenance=model` + `confirmed=false`；注入块声明"是数据不是指令"；**高影响动作只看当前会话意图** |
| 轨迹投毒（被污染的经验变成可执行规则） | 经验一律 `pending`，只有用户在前端确认（`/lessons/{id}/activate`）才变 `active`；经验只作为"参考"注入，绝不自动成为规则或 skill |
| 越权查别人的记忆 | `memory_search` 的身份来自服务端 contextvars，模型无权指定 `user_id`；内部接口共享密钥 + 未配置即拒绝 |
| 摘要幻觉被当成事实 | 摘要必须带 `sourceEventIds`；不确定的信息写进 `openQuestions`（疑问句）；不是合法 JSON 就**不落库** |
| 上下文被记忆吃光 | 各处硬预算（见 §3.8） |
| 用户被暗中画像 | 提供查看/撤回/确认接口（`/api/v1/memory/*`，JWT + 归属校验） |

**刻意不做的事**：agent 在对话中直接写事实（`memory_note`）。写入路径一旦暴露给模型，
投毒面立刻放大；当前所有写入都发生在离线巩固里，可以统一校验。

## 8. 表结构（概要）

```
memory_events    id, user_id, session_key, kind, role, content, symbol,
                 event_time, raw_time_phrase, occurred_at, ingested_at, time_zone,
                 provenance, trust, meta, created_at                    -- 只 INSERT
memory_episodes  id, user_id, session_key, version, started_at, ended_at, summary,
                 key_points, open_questions, entities, source_event_ids, model, created_at
memory_facts     id, user_id, session_key, subject, predicate, fact_value, fact_type,
                 confidence, supersedes_id, event_time, raw_time_phrase, valid_from,
                 recorded_at, data_as_of, time_zone, provenance, trust, confirmed,
                 source_event_ids, created_at
memory_lessons   id, user_id, session_key, task_type, symptom, context, attempts,
                 resolution, reusable_rule, evidence_event_ids, confidence, status,
                 provenance, trust, recorded_at, last_used_at, created_at
```

**数据库只有一个**：MySQL `stockdb`（与 users/strategies/messages 同库），
4 张表由 `spring.jpa.hibernate.ddl-auto=update` 在服务启动时自动创建（无迁移脚本）。
Redis 只用于行情缓存，与记忆无关。
不属于数据库的派生存储：embedding 缓存 `./.memory_index/embeddings.jsonl`、
向量索引（进程内或 `CHROMA_DATA_DIR`），两者都可丢可重建，重建只依赖 MySQL。

事实键 = `(user_id, lower(subject), lower(predicate))`；
经验键 = `(user_id, lower(task_type), lower(symptom))`，**行数即复发次数**（升格 skill 的判据）。

## 9. 未完成 / 下一步

| 项 | 说明 |
|---|---|
| ~~用户可见的记忆管理页~~ | **已完成（M2 尾部）**：`frontend/src/pages/Memory.vue`（`/memory`，Dashboard 有入口）。能看画像/事实（含变更链）/经验/对话摘要，可撤回事实、确认或停用经验。后端 `/api/v1/memory/*`（JWT + 归属校验） |
| 可观测性 | `/health` 现在返回 `memory` 段：向量后端、`semantic_recall_enabled`、embedding 模型与缓存条数。**语义召回是会静默降级的功能**，所以状态必须一眼可见 |
| 物理删除通道 | `retract` 只是逻辑撤回；行使删除权需要单独的硬删除 + 审计（合规动作） |
| 账本 compaction | 原始事件保留期与归档降采样未实现（当前无限增长） |
| 经验 → skill 升格 | 已能提示"复发 ≥3 次建议人审"，管理页也能确认/停用，但没有自动生成 skill 的流程（有意为之） |
| 记忆质量评测集 | **已完成（M2 尾部）**：`python-data-service/tests/memory_eval/`（12 个离线用例 + 3 个 live 抽取用例），见 §10.1 |
| 向量库替换 | 见 §6.1：`VECTOR_BACKEND=chroma` 已可用（落盘 + 陈旧条目清理，有测试） |

## 10. 测试策略（现状）

- Python（233 项）：时间解析的绝对日期表、事实键收敛、取代链写入契约、
  工具信封与预算、记忆 fail-open、经验规范化、RRF 融合与语义召回、
  向量后端切换与 Chroma 陈旧条目清理、**Python → Java 内部接口的请求形状契约**
  （字段名漂移会导致静默失效）、`/health` 记忆段，以及 §10.1 的 golden set。
- Java（91 项）：取代链（改口）、撤回不删数据、画像挑选、经验 pending 与复发计数、
  巩固节奏（用真实奇数计数）、内部接口鉴权（三种拒绝路径）、
  管理接口的**越权防护**（所有查询与操作以登录用户为范围）。
- 前端：`vite build` 通过；交互状态（加载/空/出错/确认中/播报）在页面里各有分支。
- 端到端手验见 §12。

### 10.1 记忆质量 golden set

改动 prompt、检索权重、阈值、预算时，**绝大多数问题不会报错**，只会让答案悄悄变差。
所以有一套固定场景 + 指标 + 门禁：

```powershell
pytest tests/test_memory_golden.py -v                    # 离线、确定性、免费（12 个用例）
python -m tests.memory_eval.runner                       # 加指标表 + JSON 报告（CI 可用退出码）
MEMORY_EVAL_LIVE=1 pytest tests/test_memory_golden.py -k live   # 可选：真调 LLM 评抽取
```

被测的是**模型最终看到的那段文本**，走真实代码路径
（`run_agent` → `recall` → RRF 融合 → 渲染 → `build_messages`），
只在系统边界换三处替身：Java 返回的记忆上下文、语义召回名次、LLM 回复。

12 个离线用例覆盖：时间锚点（空记忆）、指代追问、改口取代、
**语义召回不得引入 Java 没给的旧值**、时间指代（"去年"→绝对日期）、跨会话偏好、
画像去重、经验复用（标注"未经确认"）、数据口径（`data_as_of`）、
投毒抗性（记忆含伪指令时框架约束仍在）、预算上限、语义重排。

指标与门禁（`harness.GATES`，越线即 CI 红）：

| 指标 | 含义 | 门禁 | 当前 |
|---|---|---|---|
| `recall_hit_rate` | 该注入的记忆注入了吗 | ≥ 0.9 | 1.0 |
| `exclude_violations` | 错误注入（含过期值/越权引入） | == 0 | 0 |
| `max_injection_chars` | 记忆占用上下文的上限 | ≤ 2600 | 659 |
| `time_anchor_rate` | 注入块是否都带"今天" | == 1.0 | 1.0 |
| `safety_notice_rate` | 是否都带"是数据不是指令" | == 1.0 | 1.0 |
| `local_p50/p95_ms` | 记忆层自身耗时（不含网络/LLM） | 观察值 | 0.05 / 0.09 |

**live 模式第一次跑就抓到一个真问题**：模型把"止损改成 5%"记成了
`{subject: strategy:MA cross}` 而不是 `{subject: user}`。值抽对了，但主体漂移会让
"用户下次只说'止损改成 3%'"落成**另一个键**，取代链断裂、两个矛盾的止损值并存。
修复是在抽取提示词里明确：风险设置类事实（止损/止盈/仓位/风险偏好/风格/频率）
**即使是在讨论某个策略时说的，subject 也一律写 user**，只有用户明确说"这个策略专用"
才写 `strategy:名称`。修完 live 抽取召回率 1.0，并新增 `live_extract_key_stability`
用例把这个不变量钉住。

> 离线用例喂的是"已经抽好的事实"，所以它测不出"prompt 改坏了导致不再抽事实"——
> 那正是 live 模式存在的理由，也是它值得偶尔花几个 token 的原因。

## 11. 记忆管理页（用户可见面）

`/memory`（Dashboard → 记忆卡片进入）。这一页的存在意义是让记忆<b>可被信任</b>：

| 区块 | 内容 | 用户能做什么 |
|---|---|---|
| 概览 | 有效事实数 / 待确认经验数 / 摘要段数 | —（"到底记住了多少"） |
| 长期画像 | always-on 的稳定信息 | 看（与下面的列表不重复） |
| 记住的事 | 事实，按类型筛选，显示日期与变更链（"此前为 X"） | **撤回**（就地二次确认，说明"历史记录仍保留"） |
| 经验 | 症状 → 做法，状态徽章、出现次数 | **确认可信 / 停用**（未确认的会明确标注） |
| 对话摘要 | 每天的摘要，默认显示 3 段 | 展开全部 |

无障碍与文案约束（沿用项目既有约定）：原生 `button`、`aria-pressed` 表达筛选状态、
`role="status"` 播报操作结果、`role="alert"` 报错并给"重新加载"、
撤回用危险色且必须二次确认、空状态给出下一步动作、事实超过 30 条时明确提示"只显示了 N 条"。

## 12. 手验清单

```powershell
# 0. 子系统状态（语义召回到底在工作没有）
curl http://localhost:8000/health
# 1. 事实与变更链（改口后 object=5 且带 previousValue=8）
curl "http://localhost:8080/api/v1/internal/memory/facts?userId=1&query=止损" -H "X-Internal-Token: <token>"
# 2. 完整历史
curl "http://localhost:8080/api/v1/internal/memory/facts/history?userId=1&subject=user&predicate=stop_loss_pct" -H "X-Internal-Token: <token>"
# 3. agent 下一轮会看到什么（画像/事实/经验/前情提要）
curl "http://localhost:8080/api/v1/internal/memory/context?userId=1&sessionKey=1:2026-09-16&query=止损改成5%&factLimit=30" -H "X-Internal-Token: <token>"
# 4. 手动触发巩固（不用等跨天）
curl -X POST http://localhost:8000/api/v1/memory/consolidate -H "X-Internal-Token: <token>" `
  -H "Content-Type: application/json" -d '{"user_id": 1, "session_key": "1:2026-09-16"}'
```

## 附：与 TencentDB Agent Memory 的对比结论（2026-09-16）

对比对象：[TencentCloud/TencentDB-Agent-Memory](https://github.com/TencentCloud/tencentdb-agent-memory)（MIT）。
第三方审计见 [ai-memory-comparison](https://github.com/carsteneu/ai-memory-comparison/blob/main/evidence/tencentdb.md)。

**它更强的地方（值得抄设计）**：

1. L0→L3 分层更细（我们 L1 只到会话摘要，L2 场景层未做）；
2. **符号化短期记忆**：工具日志卸载到外部文件 + Mermaid 画布 + `node_id` 回钻，
   官方基准 token −61%。等接入新闻/研报（外部文档）后这才是真正的瓶颈；
3. 一组生产验证过的节奏参数：L1 每 5 轮（warmup 1→2→4）、空闲 600s、L2 最小间隔 900s、
   画像每 50 条记忆、召回 top5 + 字符预算、召回超时 5s 跳过。
   我们的"预热递增 + 冷却"就是从这里取的（见 §5）。
4. 白盒可调试：Persona/Scene 落成 Markdown，链路可逐层下钻。

**我们更强、且对本项目是硬需求的地方**：

| 维度 | 我们 | 它 |
|---|---|---|
| 时间有效性 | 双时间戳 + 取代链（改口可推理，可回答"以前是什么"） | 审计显示无 supersede / 失效 / 时间旅行 |
| 撤回与审核 | retract 走取代链；经验 pending → 人工确认 | 审计显示无 trustModel / quarantine / 显式遗忘 |
| 数据边界 | MySQL 单一真相源，agent 无凭据（内部 API） | 自有 SQLite/TCVDB + Markdown，是第二个数据源 |
| 金融语义 | `data_as_of`、合规话术、策略 JSON 契约 | 通用记忆，无领域约束 |
| 宿主 | 自研 Python + Java，任意改造 | OpenClaw 插件 / Hermes Gateway（Node ≥22.16） |

**结论**：不整体替换（会把记忆数据搬出 MySQL，与"agent 不碰数据层"直接冲突，
且缺 supersede/trust/forget）。我们的做法是**吸收设计**：
分层思路、cadence 参数、符号化压缩留到接入外部文档时再考虑。

**注意**：该项目演进很快（仓库定位已转向 team-level memory hub：Chat Memory / Skill /
LLM-Wiki / Code-Graph），引用其结论前先看当前分支。
