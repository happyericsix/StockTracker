"""客观事实（system 来源）的写入通道。

<h3>为什么需要第二条写入通道</h3>
记忆系统原本只有一条写入路径：会话巩固（`agent/consolidate.py`）。它把**用户说过的话**
整理成事实与经验。这条路径有个结构性缺口 —— **记忆里全是用户的主张，没有系统验证过的观测**：

- "这个策略回测最大回撤 18.3%"；
- "模拟盘跑到现在净值 9.7 万、现金 0、持股 100 股"；
- "上一版改进建议被用户拒绝了"。

这些信号是客观的、可复算的，而且恰恰是"改进策略"必须依赖的依据 —— 但它们一条都进不了记忆，
因为它们不是从对话里抽出来的。

所以这里定义第二条通道：**由代码产生、不是由模型产生**的事实。它与巩固路径的差别不只是
来源标注，而是四条更硬的性质：

1. `provenance=system` / `trust=high` / `confirmed=true`：它不是主张，是可复算的观测，
   因此不需要"用户确认过"才算数；
2. `confidence=1.0`：没有"模型猜的"成分，也就不该被置信度阈值筛掉；
3. **键由代码定义**（见 `PREDICATES`）：模型看不见这条通道，因此不可能往里面写东西
   —— 这是一条**单写入者**的旁路，不是给模型开的第二个口子；
4. 与用户事实**共用同一条取代链**：同一个键上的新值取代旧值，于是"这只策略的回撤在变好
   还是变差"变成一次取代链查询，而不是让模型去比两段自然语言。

<h3>为什么谓词必须是白名单</h3>
取代链能工作的前提是**键稳定**（`agent/facts.py` 用别名表把"止损/止损线"收敛成
`stop_loss_pct`，就是为了这件事）。代码产生的键如果一处写 `backtest_drawdown`、
另一处写 `backtest_max_drawdown_pct`，系统里就会出现两个"回撤"事实**永远互相不取代** ——
这是最难发现的一类错：每条单独看都对，合起来自相矛盾。

所以未知谓词**直接丢弃并告警**，不做宽容写入。白名单的另一半在 Java 侧
（`ObjectiveFactKeys.java`），由 `tests/test_objective_facts.py` 的两边一致性测试钉住
—— 这个项目里"两边名字对不上就静默失效"已经发生过太多次（user_id / user_name）。

<h3>subject 用数字 id，而不是策略名</h3>
巩固提示词里，模型抽出的策略事实写 `strategy:名称`。这里刻意用 `strategy:<数字 id>`：
名称会改、会重名，而 id 是稳定标识，取代链必须挂在稳定标识上。两套命名空间**不合并**
—— 合并需要一个"名称 → id"的映射，而那个映射由持有数据的 Java 侧掌握，
记忆层不该去猜。

<h3>今天的真实写入方</h3>
回测与模拟盘结算这两类事实由 **Java 侧**写入（它是数据的持有者：只有它知道
strategyId 与账户状态）。所以本模块的角色是**契约的单一真相源** + 给 Python 侧
（将来的策略教练、建议闭环）用的写入器；`record_facts` 就是那条通道的入口。
"""
from __future__ import annotations

import logging
from typing import Optional

from agent import memory_store

logger = logging.getLogger(__name__)

# ==================== 来源标注 ====================
# system 是本通道唯一的 provenance：它回答"这句话是谁说的" —— 答案是"我们的代码算出来的"，
# 既不是用户说的（user），也不是模型推断的（model），更不是第三方内容（external）。
PROVENANCE_SYSTEM = "system"
TRUST_HIGH = "high"
CONFIDENCE_CERTAIN = 1.0

# 只用这两种类型：观测（可复算的量）与决定（系统做过的事）。
# 不用 preference/constraint/goal/holding —— 那四类描述"用户是谁"，代码无权认定。
FACT_TYPE_OBSERVATION = "observation"
FACT_TYPE_DECISION = "decision"
ALLOWED_FACT_TYPES = (FACT_TYPE_OBSERVATION, FACT_TYPE_DECISION)

# 与 facts.MAX_VALUE_CHARS 同口径：客观事实是标量（数字/短文本），不是段落
MAX_VALUE_CHARS = 200

# ==================== 谓词白名单（与 Java 的 ObjectiveFactKeys 必须一致） ====================
# 命名规则：能算出来的量一律带单位后缀（_pct / _count / _at），
# 这样"18.3 是百分比还是小数"这个老问题在键名里就答完了。
PREDICATES = (
    # —— 回测：每跑一次都会在同一批键上形成取代链，于是"改完参数是变好还是变差"可查 ——
    "backtest_total_return_pct",
    "backtest_buy_and_hold_return_pct",
    "backtest_excess_return_pct",
    "backtest_max_drawdown_pct",
    "backtest_sharpe",
    "backtest_win_rate_pct",
    "backtest_trade_count",
    "backtest_at",
    # —— 模拟盘：每日结算写一次（**不是**每次价格刷新，见 PaperTradingService 的说明）——
    "paper_equity",
    "paper_cash",
    "paper_shares",
    "paper_return_pct",
    "paper_last_eval_at",
    "paper_max_drawdown_pct",
    "paper_flat_days",
    # —— 样本外验证（多标的 × 多时段）：由 Java 侧跑完矩阵后写入 ——
    # "这条规则到底行不行"的可复算回答。放进客观事实通道是为了能被**引用**：
    # 盘后复盘引用它，讨论协议拿它当裁决依据 —— 而写在对话里的话下一轮就找不到了。
    "verify_at",
    "verify_valid_cells_count",
    "verify_beat_buy_and_hold_count",
    "verify_avg_excess_pct",
    "verify_fee_drag_pct",
    "verify_engine_version",
)
PREDICATE_SET = frozenset(PREDICATES)

# 刻意**还没有**的键（写在注释里，而不是先占位再忘记）：
#   （paper_max_drawdown_pct / paper_flat_days 已于 2026-09-18 补上 —— 每日净值快照落地后，
#    这两个量才真的算得出来；在此之前它们只是"看起来该有"的键。）
#   verify_* 的逐标的明细（每个标的/时段一行）
#      —— 客观事实是标量。明细留在 backtest-matrix 的响应与痕迹里，
#         事实通道只留**汇总的样本量与结论**，否则取代链会被几十个键撑爆。
#   advice_last_outcome / advice_prediction_hit
#      —— 属于建议闭环（采纳/拒绝、预测是否命中），等教练角色落地后再加。
# 先占位的键是最糟的选择：它会让"这条通道写入了什么"变得不可回答。


def strategy_subject(strategy_id) -> str:
    """策略事实的 subject：`strategy:<数字 id>`（见模块 docstring 的命名空间说明）。"""
    text = str(strategy_id if strategy_id is not None else "").strip()
    return f"strategy:{text}" if text else ""


def build_fact(subject, predicate, value, *, fact_type: str = FACT_TYPE_OBSERVATION,
               event_time=None, data_as_of=None, raw_time_phrase=None) -> Optional[dict]:
    """构造一条客观事实。**不合法就返回 None 并告警，不做宽容修补。**

    拒绝的情况（每一种都是"静默写坏键"的前兆，宁可这次不写）：
    - subject 为空、predicate 不在白名单；
    - value 为 None，或者是个 dict/list（客观事实是**一个标量**；
      结构化结果属于 `offload` 的落盘机制，不是记忆）。
    """
    subject_text = str(subject or "").strip()
    predicate_text = str(predicate or "").strip()
    if not subject_text:
        logger.warning("客观事实缺少 subject，已跳过 predicate=%s", predicate_text or "?")
        return None
    if predicate_text not in PREDICATE_SET:
        # 不宽容：写进去就会变成一条永不与其它值取代的孤儿事实
        logger.warning("未知客观事实谓词 %r 已丢弃（白名单见 agent/objective.py）", predicate_text)
        return None
    if value is None:
        return None
    if isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, (int, float)):
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            # NaN/Infinity 不是可比较的观测：写进去只会取代掉一个真实的值
            logger.warning("客观事实的值不是有限数（%s），已丢弃", value)
            return None
        # 浮点只保留 3 位，且**整数值的浮点归一成整数**：
        # 回测指标本来就带噪声，多留几位只会让取代链"每天都在变"；
        # 而归一更重要 —— 同一个量在 JSON 里可能是 7 也可能是 7.0，
        # 不归一就会出现"值没变、取代链却多了一条"的假变更。
        # 这条规则与 Java 侧 ObjectiveFactKeys.format 必须一致。
        rounded = round(number, 3)
        value = int(rounded) if rounded.is_integer() else rounded
    elif isinstance(value, (dict, list, tuple, set)):
        logger.warning("客观事实的值必须是标量，收到 %s（谓词 %s）已丢弃",
                       type(value).__name__, predicate_text)
        return None

    value_text = str(value).strip()
    if not value_text:
        return None
    if len(value_text) > MAX_VALUE_CHARS:
        value_text = value_text[:MAX_VALUE_CHARS]

    kind = str(fact_type or FACT_TYPE_OBSERVATION).strip().lower()
    if kind not in ALLOWED_FACT_TYPES:
        kind = FACT_TYPE_OBSERVATION

    return {
        "subject": subject_text[:128],
        "predicate": predicate_text,
        "object": value_text,
        "fact_type": kind,
        "confidence": CONFIDENCE_CERTAIN,
        "event_time": event_time,
        "raw_time_phrase": raw_time_phrase or None,
        "data_as_of": data_as_of,
        "provenance": PROVENANCE_SYSTEM,
        "trust": TRUST_HIGH,
        "confirmed": True,
    }


def record_facts(user_id, session_key, facts, source_event_ids=None) -> list:
    """把一批客观事实写进记忆，返回 Java 侧的处置结果（created / superseded / unchanged）。

    **永不抛异常**：与记忆系统的其它入口同一条纪律 —— 记忆是增强功能，
    回测/结算绝不能因为记忆写不进去而失败。失败只记日志。
    """
    payload = [fact for fact in (facts or []) if isinstance(fact, dict)]
    if not user_id or not session_key or not payload:
        # 没有身份就没有记忆：不猜、不写孤儿事实（孤儿事实查不出来是谁的）
        if payload and not (user_id and session_key):
            logger.debug("缺少 user_id/session_key，跳过 %d 条客观事实", len(payload))
        return []
    try:
        result = memory_store.save_facts(user_id, session_key, payload,
                                         source_event_ids=source_event_ids)
    except Exception as exc:  # noqa: BLE001
        logger.warning("客观事实写入失败 user=%s: %s", user_id, exc)
        return []
    items = result.get("results") if isinstance(result, dict) else None
    return items if isinstance(items, list) else []


def record_backtest(user_id, session_key, strategy_id, backtest, *, ran_at=None) -> list:
    """把一次回测的指标记成客观事实（Python 侧的写入器；Java 走同名键自己写）。

    `backtest` 是回测引擎的原始返回。只记**确实出现在返回里**的字段：
    缺字段时宁可不写，也不要补一个 0 —— 一个假的 0 会取代掉上一次真实的回撤值，
    而"回撤突然变成 0"比"没有这次记录"危险得多。
    """
    if not isinstance(backtest, dict):
        return []
    subject = strategy_subject(strategy_id)
    mapping = (
        ("total_return_pct", "backtest_total_return_pct"),
        ("buy_and_hold_return_pct", "backtest_buy_and_hold_return_pct"),
        ("excess_return_pct", "backtest_excess_return_pct"),
        ("max_drawdown_pct", "backtest_max_drawdown_pct"),
        ("sharpe_ratio", "backtest_sharpe"),
        # 引擎里这个字段叫 win_rate（没有 _pct 后缀），但它的单位是百分比。
        # 键名统一带 _pct，映射放在这一处，避免调用方各自解读。
        ("win_rate", "backtest_win_rate_pct"),
        ("trade_count", "backtest_trade_count"),
    )
    facts = []
    for source_key, predicate in mapping:
        value = backtest.get(source_key)
        # 只记**确实是数字**的字段，与 Java 侧 addMetric 的 isNumber() 同一口径。
        # 字符串（"N/A"）与缺失一律跳过：一个 "N/A" 会取代掉上一次真实的回撤值，
        # 而回撤正是"改进策略"要读的那个数 —— 写坏比不写危险得多。
        # 布尔要单独排除：Python 里 True 是 int 的子类，会混进数值判断。
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if isinstance(value, float) and value != value:  # NaN：不是可比较的观测
            continue
        facts.append(build_fact(subject, predicate, value, data_as_of=ran_at))
    if ran_at:
        facts.append(build_fact(subject, "backtest_at", ran_at, data_as_of=ran_at))
    return record_facts(user_id, session_key, [f for f in facts if f])


def record_paper_snapshot(user_id, session_key, strategy_id, account, *, as_of=None) -> list:
    """把模拟盘的一次结算记成客观事实。

    `account` 是账户快照（含 equity / cash / shares / initial_capital）。
    `paper_return_pct` 在这里算，而不是让调用方各算一遍：口径只该有一处。
    """
    if not isinstance(account, dict):
        return []
    subject = strategy_subject(strategy_id)
    facts = []
    for source_key, predicate in (("equity", "paper_equity"),
                                 ("cash", "paper_cash"),
                                 ("shares", "paper_shares")):
        if source_key in account:
            facts.append(build_fact(subject, predicate, account.get(source_key),
                                    data_as_of=as_of))
    initial = account.get("initial_capital")
    equity = account.get("equity")
    if isinstance(initial, (int, float)) and initial and isinstance(equity, (int, float)):
        facts.append(build_fact(subject, "paper_return_pct",
                                (float(equity) - float(initial)) / float(initial) * 100.0,
                                data_as_of=as_of))
    if as_of:
        facts.append(build_fact(subject, "paper_last_eval_at", as_of, data_as_of=as_of))
    return record_facts(user_id, session_key, [f for f in facts if f])


def describe() -> dict:
    """给 /health 与调试用：这条通道定义了哪些键、各属于哪一类。"""
    return {
        "provenance": PROVENANCE_SYSTEM,
        "trust": TRUST_HIGH,
        "confirmed": True,
        "fact_types": list(ALLOWED_FACT_TYPES),
        "predicates": list(PREDICATES),
        "predicate_count": len(PREDICATES),
        "subjects": {"strategy": "strategy:<数字 id>"},
    }
