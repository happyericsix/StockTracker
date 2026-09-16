# 边界控制设计（能力 · 资源 · 数据 · 副作用 · 失败）
> 状态：待评审 · 日期：2026-09-16 · 关联：`2026-09-16-tool-layer-design.md`（T0 策略层 / T1 装填 / T2a′ 外部边界）
>
> 本文只谈**边界**：agent 能做什么、能做多少、能看谁的数据、什么动作要点头、边界自己坏了怎么办。
> 不谈能力扩张（新数据源、新工具），因为"先把笼子修好，再往里放东西"。

---

## 1. 先拆词：这里的"边界"是五种不同的东西

混在一起谈必然变成"要加强安全意识"这种没法落地的结论。它们各自的读者、失败方式、验证方法都不同：

| | 边界 | 回答的问题 | 违反时的形态 | 主要读者 |
|---|---|---|---|---|
| **B1** | 能力边界 | 这次调用**允许**吗 | 越权：调了不该调的工具 | 安全 / 权限 |
| **B2** | 资源边界 | 这次调用**划得来**吗 | 刷爆：配额、账单、线程、上游封禁 | 运维 / 成本 |
| **B3** | 数据边界 | 这份数据**能信、能看**吗 | 越权读、注入、投毒 | 合规 / 数据 |
| **B4** | 副作用边界 | 这个动作**能不能撤回** | 重复下单、不可逆损失 | 业务 / 用户 |
| **B5** | 失败边界 | 边界**自己坏了**怎么办 | 边界静默消失（最危险） | 运维 |

**B5 是这一轮的重点**：本项目已经有一批不错的边界机制，但审计发现其中最要命的不是"缺了某条规则"，
而是**几条规则在特定条件下会静默失效**（见 §3.3）。边界的价值等于它最弱那条路径上的强度。

---

## 2. 现状矩阵（先看清有什么）

| 机制 | 现状 | 位置 |
|---|---|---|
| 工具策略声明（层/副作用/审批/重试/缓存/成本） | ✅ 有，且启动自检强制自洽 | `agent/tool_spec.py` |
| 能力授权（scopes）+ 权限即可见性 | ✅ 有 | `tool_pipeline.check_scope` / `tool_sets.visible_to` |
| 一轮配额（12 次 / 重操作 3 次） | ✅ 有 | `agent/tool_scope.py:ToolBudget` |
| 同轮只读复用（省重复调用） | ✅ 有 | `TurnCache` + `ToolSpec.cache_key` |
| 有界执行池（超时 / 饱和拒绝） | ⚠️ 有，但是**全局**池 | `tool_pipeline.BoundedToolPool` |
| 外部源门禁与冷却 | ✅ 有（进程内） | `agent/external_source.py` |
| 外部内容数据块 + "不是指令" | ✅ 有 | `tool_contract.to_tool_content` |
| 账本不含外部正文 | ✅ 有（本轮钉住） | `react_agent._tool_log_entry` |
| 记忆隔离（身份服务端注入 + Java 校验归属） | ✅ 有 | `tool_scope` / `MemoryController` |
| 内部接口鉴权 | ⚠️ Java fail-closed / **Python fail-open** | `InternalMemoryController.authorized` vs `app.py:require_internal_token` |
| L3 审批门禁 | ⚠️ 门在，**没有通道** | `tool_pipeline.check_approval`（`approvals` 永远是空集） |
| 审计脱敏 | ❌ 字段在，**没人用** | `ToolSpec.redaction` / `audit_fields()` |
| 跨请求配额 / 按用户限流 | ❌ 没有 | —— |
| 成本计量（token / 外部调用落库） | ❌ 没有 | —— |
| 拒绝的可观测（计数、原因分布） | ❌ 只有日志 | —— |
| 单一入口 | ❌ **三个入口**，其中一个绕过策略 | `react_agent` / `app.py:agent_diagnostic` / 领域端点 |

---

## 3. 现状审计：三类问题

### 3.1 真缺的（机制不存在）

**① 配额是"一轮内的礼貌"，不是边界**
`ToolBudget` 是每次 `run_agent` 新建的对象：进程内、请求内、重启即清零、多实例各算各的。
它能防"模型自己绕圈"，但防不了"有人连发 500 条消息"。`POST /api/v1/chat/send` 只检查了非空
（`ChatController.send`），之后每条消息都能触发最多 `MAX_STEPS=12` 轮 LLM + 工具调用。

**② 没有任何成本计量**
`cost_class` 只用于"每轮重操作 ≤ 3 次"这一个判断。LLM 的 token 用量只在旧路径记了一行日志
（`llm_service.chat` 里的 `logger.info(tokens=...)`），`chat_completion`（agent 真正走的那条）
**完全不看 usage**。外部调用只在进程内计数。于是"这个功能一个月花多少钱"无法回答。

**③ 拒绝没有可观测面**
`policy_denied` / `budget_exceeded` / `source_unavailable` 只写日志与账本，没有计数。
"昨天有多少次越权尝试""外部源被限流了几次"这类问题现在只能翻日志。

**④ 工具池是全局的，不公平**
`BoundedToolPool(capacity=8, workers=4)` 是模块级单例（`EXECUTOR`）。
一个用户卡死的 akshare 调用会一直占着名额（**这是刻意的**：宁可拒绝也不无限堆积），
但代价是**另一个人也会拿到 `tool_busy`** —— 别人的故障变成了你的故障。

### 3.2 看起来有、其实是装饰的（最该先修）

| 装饰品 | 证据 | 危害 |
|---|---|---|
| `RateLimitException` + `GlobalExceptionHandler.handleRateLimit` | grep 全仓**没有任何 throw 点** | 有一张"限流已实现"的皮，读代码的人会以为做了 |
| `ToolSpec.redaction` / `audit_fields()` | 除声明与 getter 外**无任何读取方** | 一旦出现带敏感参数的工具（如资金账号），审计日志会直接落明文 |
| `approvals` 字段 | `run_agent` 调 `tool_scope.begin(...)` 时**从不传** approvals | L3 工具一进来，`needs_approval` 就是一条**死路**：模型被拒，但没有任何办法拿到审批 |

装饰字段比没有字段更危险：它让"检查清单"看起来是绿的。

### 3.3 静默失效（B5：边界自己坏了）

**① Python 侧内部鉴权 fail-open**
`app.py:require_internal_token` 的语义是"配了 token 才校验"。
于是 `.env` 丢一行、部署时忘了设环境变量，`/api/v1/agent/chat`、`/api/v1/memory/consolidate`、
`/api/v1/strategies/*`、以及新增的 `/api/v1/external/archive/{id}`（**能读回外部原文**）
就全部变成**无鉴权**。同一个信任边界上，Java 侧（`InternalMemoryController.authorized`）
未配置 token 时是"一律拒绝"，Python 侧是"一律放行"—— 两种默认值，这本身就是缺陷。

**② 工具层不是单一入口**
`app.py:agent_diagnostic` 直接调 `tool_registry.execute_tool(name, args)`，
**完全绕过管线**：没有 ToolContext、没有 scope 检查、没有预算、没有外部源门禁。
今天它只调 `get_risk_metrics` / `get_model_status`（都是 L2 只读），爆炸半径小；
但这个端点是"以后加工具时顺手复用一下"的天然候选，而它不会报错、只会悄悄跳过所有策略。

**③ 上游是明文 HTTP**
行情走 `http://qt.gtimg.cn`（`akshare_client.py`）。公开行情篡改动机低，但**所有结论都建立在它上面**，
而现在没有任何异常检测：被喂一个假价格，agent 会认真地为它写出一份分析。
（THS 客户端是 HTTPS，这点没问题。）

---

## 4. 五条设计原则（从审计反推）

1. **默认拒绝，且默认值写在代码里**：安全相关的默认值不允许"未配置=放开"。
   未配置要么拒绝，要么在启动时**大声失败**，不能等到有人扫到才被发现。
2. **边界必须有明确的三类语义**，不能混：`拒绝`（这次不该做）/ `故障`（做不成）/ `降级`（能做但打折）。
   模型、用户、运维看到的是三类不同的东西（这条 T0 已立，继续沿用）。
3. **先能看见，再能限制**：没有计量就没有限流 —— 限多少是拍脑袋的；
   有了计量才能回答"限 20 条/分钟会不会伤到正常用户"。
4. **边界状态必须可观测、可测试**：每条边界都要能在 `/health` 或指标里看到当前状态，
   并且有一条**会在 CI 里失败**的测试。做不到这两点的边界，会在半年后变成 §3.2 的装饰品。
5. **一个入口**：所有工具调用只允许一条路径。做不到"物理上一个入口"，就用**测试**保证
   "没有第二条路径"（见 P4 的入口守卫测试）。

---

## 5. 分期方案

排序依据：**P1 → P2 → P4a → P3 → P5**。
理由：先有计量（才能定阈值），再有跨请求限制（真正防刷），再做数据收口与入口收口（便宜且立即见效），
审批（P3）等第一个 L3 工具出现前完成即可，运维开关（P5）最后。

### P1 计量与账本（0.5–1 天）★ 一切的前置

**改什么**

| 文件 | 改动 |
|---|---|
| `agent/metering.py`（新） | 进程内计量：按 `tool / dataset / llm` 分类累加 `calls / failures / latency / result_chars / cached / truncated`；拒绝按 `error_code` 分类计数；提供 `snapshot()` |
| `agent/tool_pipeline.py` | 执行成功/失败/拒绝三个出口各记一笔（含 `code` 与 `cost_class`） |
| `agent/external_source.py` | 已有 `calls / cache_hits` 并入统一计量（保留现有 `/health` 字段，别破坏契约） |
| `llm_service.chat_completion` | 返回体里带上 `usage`（DeepSeek 兼容 OpenAI，`usage.total_tokens` 可用），循环里累加 |
| `agent/react_agent.py` | 一轮结束时把 `{user_id, session_id, llm_calls, llm_tokens, tool_calls, refusals, duration_ms}` 追加成一条账本事件（`kind=usage`）——**复用已有的 `memory_events` 与批量写入通道，不新建表** |
| `app.py:/health` | 增加 `metering` 段：分工具/分数据集的调用数与拒绝原因分布 |

**关键取舍：不新建表、不引入 Prometheus。**
第一阶段的目标是"能回答问题"，不是"能画图"。账本已经是追加式、有 `kind` 字段、能被 Java 查询，
`kind=usage` 一条事件就够支撑"这个月用了多少 token / 被拒了多少次"。

**验收（可测）**
- 跑一轮对话后，`memory_events` 里出现一条 `kind=usage`，字段与实际调用一致（单测 + 一次真跑）；
- `/health` 的 `metering` 段显示工具调用数、`source_unavailable` 次数、LLM token 累计；
- 断言：被策略拒绝的调用**不计入** `tool_calls`，只计入 `refusals`（否则指标会自欺）。

### P2 跨请求配额与限流（1–2 天）★ 真正的"边界控制"

分三层，缺一层都有洞：

**L1 用户级（防刷）**
- 新增 `ChatRateLimiter`（Java 侧，内存滑动窗口；**先不上 Redis**，等真多实例再说）：
  默认 20 条/分钟、200 条/天，超限返回 `429` + `Result.error(429, "消息太频繁，请稍后再试")`
  —— 顺手让 §3.2 的 `RateLimitException` **第一次被真的抛出**，从装饰品变成机制。
- 位置很关键：放在 `MessageService.handleChatSend` **最前面**，早于落库与入账。
  否则刷消息仍会把 `message` 表与账本写满 —— 限流要挡在"产生持久副作用"之前。
- 消息长度上限（Java 侧 `ChatController`）：单条 ≤ 2000 字符（与 `memory.MAX_MESSAGE_CHARS` 对齐），
  超限直接 400。现在这条上限只在 Python 侧截断，意味着超长消息仍会写进库。

**L2 轮次级（防绕圈）**
- `ToolBudget` 保留，但把 `MAX_STEPS=12` 从常量改成**由声明推导**的上限
  （`max_llm_steps_per_turn`），并新增 `max_llm_tokens_per_turn`；
- 明确"配额耗尽"的语义是 `budget_exceeded`（已有的错误码），模型看到的是"还剩几次"，不是"崩了"。

**L3 全局与公平（防连坐）**
- 把 `BoundedToolPool` 从"全局 8 个名额"改成**按用户分桶**：每个用户独立名额（如 3），
  全局上限（如 12）。一人卡死最多占满自己的桶，别人不受影响 —— 现在的全局池会让
  别人的超时变成你的 `tool_busy`。
- 池满时的语义保持 `tool_busy` + `retryable=True`，但文案要区分"你自己忙"和"系统忙"。

**外部源每日上限**
- `external_source` 增加"每日调用上限"（计数落 P1 的账本，启动时加载当天计数）；
- 超限的语义**不是报错**而是**降级**：返回缓存、或 `source_unavailable` 里明说"今日额度已用完"。
  这与"源挂了"必须区分开（一个是我们的选择，一个是别人的故障）。

**验收**
- 20 条/分钟的消息流 → 第 21 条返回 429，且**没有新的 message / 账本行**；
- 超长消息返回 400，库里没有超长行；
- 并发测试：一个用户占满自己的工具名额时，另一个用户的工具调用**正常返回**；
- 单测：拒绝路径不产生副作用（不落库、不入账、不扣外部额度）。

### P3 审批通道 + 副作用边界（1–2 天，可与 P4 并行）

改的是"门是死的"这件事。最小可用形态（不引 MCP 的 MRTR，先用前端确认）：

1. `needs_approval` 返回值里带 `approval_id` 与 `action` 摘要；
2. Java 存 pending approval（新表 `approval_request`：user / tool / args_hash / status / expires_at）；
3. 用户在前端点确认 → Java 把该工具的 `approvals` 随下一次请求下发给 agent（**短期令牌，一次性**）；
4. 幂等：`args_hash` + `idempotency=key` 落到 DB 唯一索引 —— "approved call is the executed call"，
   重复请求不会执行两次；
5. 审计：审批与拒绝进同一张表，可查询。

**验收**：用一个假的 L3 工具（例如 `save_strategy_to_library`）跑完
**拒绝 → 确认 → 执行 → 重复请求不重复执行 → 过期确认失效** 五步。

### P4 数据边界与入口收口（1 天，性价比最高）

**a) 内部鉴权 fail-closed（安全默认值）**
- `INTERNAL_API_TOKEN` 未配置时：**拒绝**所有 `/api/v1/*`，并把 `/health` 标成 `degraded`
  （`/health` 本身保持可访问，否则运维没法判断为什么全挂了）；
- 与 Java 侧行为对齐（`InternalMemoryController` 已经是 fail-closed）；
- `.env.example` 给出默认值，本地开发零阻力。

**b) 单一入口守卫（防未来）**
- 把 `agent_diagnostic` 改成走 `tool_pipeline.call_tool_bounded`（带一个"内部诊断"身份的 ToolContext）；
- 加一条**结构测试**：扫描 `app.py` / `agent/`，断言除 `react_agent._execute_tool_bounded`
  与管线自身外，没有其它 `execute_tool(` 调用点（白名单机制）。
  这类测试的价值在于：它拦的是"以后顺手写的那一行"，而不是今天的代码。

**c) 脱敏真正生效**
- 审计/账本写入前按 `ToolSpec.redaction` 替换为 `"***"`（`audit_fields()` 才有意义）；
- 断言：带 `redaction=("account",)` 的假工具，账本里不出现原值。

**d) 记忆投毒面收口**
- `consolidate` 抽取事实/经验时**排除** `provenance=external` 的事件（现在是"正文没进账本"，
  但那是隐式的；把规则写成显式过滤 + 断言，避免以后有人往账本里塞外部正文时踩雷）。

**e)（低优先，记录在案）上游完整性**
- 行情是明文 HTTP。短期不做 TLS（上游只有这个接口），但可以加**异常值检查**：
  单日涨跌幅 > 阈值、价格与昨收差异过大时标记 `data_suspect=true`，
  让模型在回答里带上"该行情数据异常"的提示，而不是直接当成事实。

### P5 运维开关与熔断（0.5–1 天）

- **kill switch**：`DISABLED_TOOLS=get_news,get_quotes` / `DISABLED_SOURCES=eastmoney`（env，启动读取；
  装填阶段直接摘除，行为与"源挂了"一致但原因不同）；
- **LLM 熔断**：连续 N 次失败 → 快速失败（不再等 30s 超时）+ 降级回复，
  避免"上游慢"变成"我们的线程全占满"；
- `/health` 显示熔断状态与剩余冷却。

---

## 6. 优先级与建议起点

| 期 | 内容 | 工期 | 为什么这个顺序 |
|---|---|---|---|
| **P1** | 计量与账本 | 0.5–1 天 | 没有它，P2 的阈值只能拍脑袋；它本身也是"回答成本问题"的能力 |
| **P2** | 跨请求配额 + 公平池 | 1–2 天 | 用户说的"边界控制"主要指这个；也是唯一能防住"有人刷爆"的一层 |
| **P4** | fail-closed + 单一入口 + 脱敏 | 1 天 | 便宜、立即见效，且防止未来踩坑（含一条结构性防退化测试） |
| **P3** | 审批通道 | 1–2 天 | 等第一个 L3 工具出现前完成即可；现在做会没有真实场景可验 |
| **P5** | 开关与熔断 | 0.5–1 天 | 运维向，前四期做完再收尾 |

**建议起点：P1 + P4a/P4b 一起做（约 1.5 天）。**
P1 给出数字，P4a/P4b 关掉两个"静默失效"（fail-open 鉴权、绕过管线的入口）——
这两件事都不改变正常行为，风险极低，却把边界从"看起来有"变成"真的有"。
P2 紧随其后，因为它需要 P1 的数字来定阈值（例如先量一周的工具调用分布，再定每日外部调用上限）。

---

## 7. 明确不做（以及为什么）

| 不做 | 理由 |
|---|---|
| 引入 Redis / 网关做限流 | 单实例自用阶段，内存滑动窗口足够；多一个组件就多一处会挂的东西 |
| token 级计费与预算 | 现在只需要"有数"，不需要"分账"；等有多用户付费才需要 |
| 用正则审查模型输出（投资建议合规） | 误伤大于收益（"止损 -8%" 这类正常内容很容易被规则命中）；合规靠提示词 + 数据块 + 不改动作这三条硬约束 |
| 全链路分布式追踪 | 现有 request_id / trace_id 已进上下文，先用于日志关联即可 |
| 给每条边界都做仪表盘 | 先 `/health` + 账本，等真的要看趋势再上 Prometheus |

---

## 8. 风险与权衡

| 风险 | 权衡 | 缓解 |
|---|---|---|
| 限流会伤到正常用户 | 429 比"卡住不回复"好；阈值先宽松（20/分钟）并靠 P1 的数据收紧 | 阈值可配 + 429 文案明确 |
| fail-closed 让本地开发多一步配置 | 与"生产忘配就裸奔"相比，这个代价必须付 | `.env.example` + 启动日志大声提示 |
| 计量增加写入 | 异步 + 批量（复用现有 `append_events`），不阻塞回复 | 写入失败只记日志 |
| 分桶池降低单用户并发上限 | 单用户工具调用本来就是串行的（ReAct 循环逐个执行），分桶几乎不损失吞吐 | 桶大小 3 已高于实际并发 |
| P3 的审批在自用场景像"多余的手续" | 它的价值不是防用户，而是**防 agent 自作主张** | 只对 L3 生效，只读工具完全不受影响 |

---

## 9. 一句话总结

现有边界的骨架是对的（声明式 + 策略集中 + 拒绝≠故障），问题集中在两处：
**一是"一轮内的礼貌"被当成了配额**（跨请求无限制、无计量、无公平），
**二是几条边界在特定条件下会静默消失**（Python 鉴权 fail-open、诊断端点绕过管线、
`redaction`/`approvals`/`RateLimitException` 是装饰）。
所以顺序是：**先让它可见（P1）→ 再让它生效（P2）→ 顺手关掉静默失效（P4）→ 最后才是审批与开关（P3/P5）**。

---

## 10. 落地记录：P1 + P4（已完成）

### 10.1 P1 计量与账本

| 文件 | 改动 |
|---|---|
| `agent/metering.py`（新） | 线程安全计数器：每工具 `calls/ok/failed/reused/refused/latency/result_chars`、拒绝按错误码分类、LLM `calls/failures/tokens/latency`；`baseline()`/`since()` 算"这一轮花了多少"；`describe_turn()` 渲染成人话 |
| `agent/tool_pipeline.py` | **唯一入口**处记三种结果：拒绝 → `record_refusal`（**不计入** `tool_calls`）、同轮复用 → `record_tool_reuse`、真实执行 → `record_tool_call`（延迟 + 结果字符数 + 成本档） |
| `llm_service.chat_completion` | 在**底层调用**处记 usage（记在调用方 = 以后新增的调用点会漏记）；失败也记账但异常继续上抛 |
| `agent/react_agent.py` | 一轮结束追加一条 `kind=usage` 账本事件（复用 `memory_events`，**不新建表**） |
| `app.py:/health` | 新增 `metering` 段（含从外部源**派生**的用量，不重复计数） |

**实测（本机真跑一轮"600519 今天什么价位？另外最近有什么消息？"）**：

```
totals: turns 1 | tool_calls 2 | tool_failures 0 | expensive_calls 1 | llm_calls 2 | llm_tokens 9006
llm:    prompt 8420 / completion 586 / avg 2030ms
tools:  get_news calls 1 latency 41ms chars_max 2755 | get_quote calls 1 latency 78ms chars_max 325
账本:   usage | system | high | 本轮用量：LLM 2 次/9006 tokens；工具 2 次；耗时 4183ms
        meta = {"delta": {...}, "duration_ms": 4183}
```

**两个刻意的小设计**：
- 被拒绝的调用**不计入** `tool_calls`：否则"模型试了 10 次被拒 10 次"在指标上会显示成高频使用；
- 外部源的用量**从健康状态派生**而不是再数一遍：两个数字迟早会对不上，而对不上的那天没人知道该信哪个。

### 10.2 P4 收口

| 项 | 改动 | 验证 |
|---|---|---|
| **P4a fail-closed 鉴权** | `INTERNAL_API_TOKEN` 未配置时 `/api/v1/*` 一律 **503**；`/health` 保持可访问并把 `status` 标成 `degraded`，加 `internal_auth` 字段 | 单测三种情况（未配置→503、错 token→401、对 token→200）；实测 `no-token -> 401` / `with-token -> 200`；确认 Java 与 Python 的 token 一致（`sha256` 相同），Java→Python 链路不受影响 |
| **P4b 单一入口** | 诊断端点改为走 `tool_pipeline.call_tool_bounded`（带 ToolContext：scopes/预算/门禁/计量全生效）；新增**结构性守卫测试** | 守卫用 `tokenize` 而不是 grep（第一版把文档里的示例也判成违规 —— 会惩罚写注释的守卫是坏守卫）；实测诊断端点 3 次调用全部带 `spec` |
| **P4c 脱敏生效** | `_audit_args()` 是 `ToolSpec.redaction` 的**唯一读取方**：参数进 `meta.args` 前按声明盖成 `***`，并带上限（400 字符）；Java 侧 `toMap` 补上 `meta`（否则审计与用量只能靠 SQL 看） | 单测：敏感字段变 `***`、非敏感字段保留、原值不出现在 meta；实测账本 `args: {"symbol": "600519"}`、用量 `delta` 都能从接口读到 |
| **P4d 摘要不吸收外来内容** | `consolidate._build_dialog` 显式跳过 `kind=usage` 与 `provenance=external` | 单测：用量行与外部工具事件都不进摘要，**内部**工具的失败仍然保留（别过滤过头） |

### 10.3 仍然没做的（下一轮）

| 项 | 状态 |
|---|---|
| **P2 跨请求配额 / 按用户限流 / 公平池 / 外部源每日上限** | ❌ 未做 —— 这是"边界控制"的主体，P1 只是给它备好了数据（阈值要用真实分布来定） |
| P3 审批通道（L3 前置） | ❌ 未做（等第一个 L3 工具） |
| `RateLimitException` 仍是死代码 | ⚠️ 仍在 —— 它要等 P2 才第一次被真的抛出 |
| P5 运维开关 / LLM 熔断 | ❌ 未做 |
| 上游明文 HTTP 的异常值检测 | ❌ 未做（P4e，低优先） |

### 10.4 测试与回归

- Python：**355 passed / 18 skipped**（本次 +29：`test_metering.py` 15 条、`test_boundary_control.py` 14 条）
- Java：**92 tests, BUILD SUCCESS**（+1：`eventsEndpointExposesMeta`）
- 新增 `tests/conftest.py`：在导入 `app` **之前**设置 `INTERNAL_API_TOKEN`。
  fail-closed 之后，干净环境（没有 `.env`）跑测试会一片 503 —— 这个坑该由测试替我们踩，
  而不是让部署去踩。用 `setdefault` 语义，于是测试结果不依赖开发者机器上的 `.env` 内容。
