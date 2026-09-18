"""执行契约（**冻结**）：一次结算"按什么口径成交"必须显式声明，并随结果落库。

<h3>为什么需要这个文件</h3>
"模拟盘 vs 回测"的对照是模拟盘存在的**唯一理由**，而它成立的前提是两个口径一致。
现状是口径散落在代码里、隐式且不一致：

| 路径 | 成交价 | 证据 |
|---|---|---|
| 回测 `run_backtest_realistic` | **次日开盘价** + 滑点 | `strategy_engine.fill_open(f = i + 1, ...)` |
| 模拟盘（日线与实时共用） | **信号当根收盘价** + 滑点 | `evaluate_bar` 返回 `closes[idx]`；`PaperTradingService.applyBarResult` 直接拿它当 fill |

于是同一套规则在两边跑出的数字**从第一天起就不可比**，而且没有任何地方记录"这条痕迹是哪个口径"。

<h3>这个文件做什么（三件事）</h3>
1. **把口径写成封闭常量集**（决策 / 结算类型 / 成交价口径 / 复权口径 / 跳过原因），
   并配跨语言一致性测试 —— 本项目已经因为"两边字符串各写一份"栽过跟头；
2. **把口径做成"指纹"**：`ExecutionFingerprint = {fill_basis, adjust_mode, money_policy_version, engine_version}`，
   每次成交/每次回测都带上它。规则只有一条：**只有指纹相同的两份结果才允许对比**
   （`compare_allowed`）。这样"换口径"只是改一处声明，历史数据仍解释得通 ——
   **不做双轨代码**，把口径变成数据属性；
3. **钱的精度口径单处定义**（`MoneyPolicy`）：价格 4 位 / 金额 2 位 / 净值 2 位 / 收益率 4 位，
   `ROUND_HALF_UP`，一律 `Decimal`。现在的隐患是 Java 侧全程 `double`，
   而"净值 = 现金 + 股数×价格"这条恒等式一旦要逐笔对账，浮点误差会累积。

<h3>刻意不做的事</h3>
- **不做运行时可变的配置**（DB / 配置文件）：口径改动必须走代码评审，且 `/health` 暴露当前生效值。
  "灵活"的正确落点是**声明**，不是黑箱；
- **不在这里做计算**：本模块只定义口径与常量，指标求值仍在 `strategy_engine`（单一职责）。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ==================== 决策（封闭集） ====================

DECISION_BUY = "buy"
DECISION_SELL = "sell"
DECISION_SKIP = "skip"
DECISIONS = (DECISION_BUY, DECISION_SELL, DECISION_SKIP)

# ==================== 结算类型 ====================

SETTLEMENT_DAILY = "daily"
SETTLEMENT_REALTIME = "realtime"
SETTLEMENT_KINDS = (SETTLEMENT_DAILY, SETTLEMENT_REALTIME)

# ==================== 成交价口径 ====================

FILL_CLOSE = "close"                  # 信号当根收盘价（P0 日线用；回测侧需同名模式才可比）
FILL_NEXT_OPEN = "next_open"          # 次日开盘价（回测默认；模拟盘 P2 用"挂单式"才能用）
FILL_REALTIME_LAST = "realtime_last"  # 盘中最新价（实时结算；**不参与回测对照**）
FILL_BASES = (FILL_CLOSE, FILL_NEXT_OPEN, FILL_REALTIME_LAST)

# 为什么日线 P0 用 close 而不是 next_open：模拟盘在**当日 15:30** 结算，
# 而"次日开盘价"要到明天 9:30 才知道 —— 当日结算结构上给不出次日开盘价。
# 想用 next_open 就必须引入"挂单 + 两阶段结算"（P2），届时把这张映射表改一行即可。
FILL_BASIS_BY_SETTLEMENT = {
    SETTLEMENT_DAILY: FILL_CLOSE,
    SETTLEMENT_REALTIME: FILL_REALTIME_LAST,
}

# ==================== 复权口径 ====================

ADJUST_NONE = "none"   # 不复权：除权日价格真的跳空（与用户账户所见一致）
ADJUST_QFQ = "qfq"     # 前复权：除权后历史价被重算（回测用；必须记录，否则不可复现）
ADJUST_MODES = (ADJUST_NONE, ADJUST_QFQ)

# ==================== 跳过原因（封闭集） ====================
# 规则：**封闭集 + 未知一律归一成 `unknown_*` 并告警**。
# 自由字符串会让"为什么没成交"永远统计不出来；而静默丢弃会让问题消失得无影无踪。

SKIP_MARKET_CLOSED = "market_closed"                        # 非交易日（需交易日历）
SKIP_SUSPENDED = "suspended"                                # 停牌
SKIP_NO_BAR = "no_bar"                                      # 当日无 K 线（数据未出）
SKIP_DATA_UNAVAILABLE = "data_unavailable"                   # 取数失败
SKIP_WARMUP = "warmup"                                      # 指标还没算出来（窗口不够）
SKIP_LIMIT_BLOCKED = "limit_blocked"                        # 涨跌停挡单
SKIP_T1_BLOCKED = "t1_blocked"                              # T+1 当日买入不可卖
SKIP_EX_DIVIDEND_DAY = "ex_dividend_day"                    # 除权除息日（P0 跳过并标注）
SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT = "insufficient_cash_for_one_lot"
SKIP_INSUFFICIENT_CASH = "insufficient_cash"
SKIP_INVALID_PRICE = "invalid_price"
SKIP_RULE_NOT_MET = "rule_not_met"                          # 规则未命中（只出现在聚合行）
SKIP_STATE_MISMATCH = "state_mismatch"                      # 信号与账户状态对不上（已持仓却要买 / 空仓却要卖）
# agent 的输出读不懂。**它必须是一个真实的原因，而不是 `unknown_*` 的兜底**：
# "模型没说清楚"与"我们没实现这条路"是两件事 —— 前者要能被统计（频次高说明提示词或模型有问题），
# 后者说明代码有缺口。混在一起就永远分不清该修哪边。
SKIP_AGENT_UNPARSABLE = "agent_unparsable"
# agent 这一轮的**硬预算**用完了（调用次数或 token 上限）。为什么它必须是一个独立原因：
# "花光了预算所以没决策"与"模型说 HOLD"在结果上都是不动，但一个是我们该调参、一个是模型的选择。
# 混在一起，预算设得太小这件事就永远发现不了。
SKIP_AGENT_BUDGET_EXCEEDED = "agent_budget_exceeded"
# agent 依赖的模型服务不可用。同样必须与"HOLD"分开：这是我们这边的故障，不是市场的结论。
SKIP_AGENT_LLM_UNAVAILABLE = "agent_llm_unavailable"

SKIP_REASONS = (
    SKIP_MARKET_CLOSED,
    SKIP_SUSPENDED,
    SKIP_NO_BAR,
    SKIP_DATA_UNAVAILABLE,
    SKIP_WARMUP,
    SKIP_LIMIT_BLOCKED,
    SKIP_T1_BLOCKED,
    SKIP_EX_DIVIDEND_DAY,
    SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT,
    SKIP_INSUFFICIENT_CASH,
    SKIP_INVALID_PRICE,
    SKIP_RULE_NOT_MET,
    SKIP_STATE_MISMATCH,
    SKIP_AGENT_UNPARSABLE,
    SKIP_AGENT_BUDGET_EXCEEDED,
    SKIP_AGENT_LLM_UNAVAILABLE,
)

UNKNOWN_PREFIX = "unknown_"
MAX_ENUM_CHARS = 32

# ==================== 快照契约 ====================

SNAPSHOT_SCHEMA_VERSION = 1

SNAPSHOT_KEYS = ("schema_version", "bar", "indicators", "params", "fingerprint", "extra")


def normalize_enum(raw: Any, allowed: tuple, *, field: str = "value") -> str:
    """把任意输入归一成封闭集里的值；不认识的一律 `unknown_*` 并告警。

    刻意**不抛异常**：交付路径上因为一个枚举值不认识就中断结算，代价远大于
    记一条 `unknown_*`。但也不能静默丢 —— 所以告警，让未知值可见。
    """
    text = str(raw or "").strip()
    if text in allowed:
        return text
    fallback = f"{UNKNOWN_PREFIX}{text}"[:MAX_ENUM_CHARS] if text else f"{UNKNOWN_PREFIX}unspecified"
    logger.warning("执行契约：未知的 %s=%r，已归一为 %s", field, raw, fallback)
    return fallback


def normalize_skip_reason(raw: Any) -> str:
    return normalize_enum(raw, SKIP_REASONS, field="skip_reason")


def normalize_decision(raw: Any) -> str:
    return normalize_enum(raw, DECISIONS, field="decision")


def normalize_settlement_kind(raw: Any) -> str:
    return normalize_enum(raw, SETTLEMENT_KINDS, field="settlement_kind")


def normalize_adjust_mode(raw: Any) -> str:
    return normalize_enum(raw, ADJUST_MODES, field="adjust_mode")


def is_known_skip_reason(raw: Any) -> bool:
    return str(raw or "").strip() in SKIP_REASONS


def fill_basis_for(settlement_kind: Any) -> str:
    """按结算类型给出成交价口径（**唯一**的映射处）。

    未登记的结算类型 → `unknown_*`（不猜、不默认取 close）：口径猜错会让
    "这条痕迹是哪个价"永远说不清，那正是本模块要消灭的问题。
    """
    kind = str(settlement_kind or "").strip()
    basis = FILL_BASIS_BY_SETTLEMENT.get(kind)
    if basis:
        return basis
    return normalize_enum(kind, FILL_BASES, field="settlement_kind(fill_basis)")


# ==================== 钱的精度口径 ====================

@dataclass(frozen=True)
class MoneyPolicy:
    """钱的类型与舍入口径（单处定义）。

    为什么必须单处定义：净值恒等式 `equity == cash + shares × price` 要能**零容差**成立，
    而"两处各自 round"必然在某个角落对不上账。
    """

    version: int = 1
    price_scale: int = 4
    amount_scale: int = 2
    equity_scale: int = 2
    return_scale: int = 4
    rounding: str = "HALF_UP"

    # ---------- 内部 ----------

    def _quantize(self, value: Any, scale: int) -> Decimal:
        if value is None or isinstance(value, bool):
            raise ValueError(f"金额/价格不接受 {value!r}")
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
        return dec.quantize(Decimal(1).scaleb(-scale), rounding=ROUND_HALF_UP)

    # ---------- 公开 ----------

    def price(self, value: Any) -> Decimal:
        return self._quantize(value, self.price_scale)

    def amount(self, value: Any) -> Decimal:
        return self._quantize(value, self.amount_scale)

    def equity(self, value: Any) -> Decimal:
        return self._quantize(value, self.equity_scale)

    def rate_pct(self, value: Any) -> Decimal:
        return self._quantize(value, self.return_scale)

    @staticmethod
    def shares(value: Any) -> int:
        """股数一律整数（整手由结算逻辑保证，不在这里凑整）。"""
        if value is None or isinstance(value, bool):
            raise ValueError(f"股数不接受 {value!r}")
        dec = value if isinstance(value, Decimal) else Decimal(str(value))
        return int(dec)

    def describe(self) -> dict:
        return {
            "version": self.version,
            "price_scale": self.price_scale,
            "amount_scale": self.amount_scale,
            "equity_scale": self.equity_scale,
            "return_scale": self.return_scale,
            "rounding": self.rounding,
        }


MONEY = MoneyPolicy()


def equity_identity_holds(cash: Any, shares: Any, price: Any, equity: Any) -> bool:
    """净值恒等式：`equity == cash + shares × price`（**零容差**）。

    这是精度口径的验收方式：不是"看起来差不多"，而是逐笔必须成立。
    """
    computed = MONEY.equity(Decimal(str(cash)) + Decimal(str(shares)) * Decimal(str(price)))
    return computed == MONEY.equity(equity)


# ==================== 决策来源（封闭集） ====================
# <h3>为什么"谁做的决定"是口径的一部分</h3>
# 一条净值曲线如果在中间换了决策方式，它就是**两条曲线**。
# 把决策来源放进指纹，{@code compare_allowed} 会自动判它们不可比 ——
# 而不是让"规则段"和"agent 段"混成一条看不出问题的线
# （混起来最省事，代价是那条线的历史含义永远说不清）。

DECISION_MODE_RULE = "rule"     # 策略 DSL 的确定性规则
DECISION_MODE_AGENT = "agent"   # 多角色 agent 辩论后给出的决策
DECISION_MODES = (DECISION_MODE_RULE, DECISION_MODE_AGENT)


def normalize_decision_mode(raw: Any) -> str:
    return normalize_enum(raw, DECISION_MODES, field="decision_mode")


# ==================== 执行指纹 ====================

@dataclass(frozen=True)
class ExecutionFingerprint:
    """一次结算/一次回测的"口径指纹"。

    只有指纹相同的两份结果才允许对比 —— 这是"单轨还是双轨"的答案：
    **不做双轨代码，做口径标签 + 对比前校验**。
    """

    fill_basis: str
    adjust_mode: str
    money_policy_version: int
    engine_version: str
    decision_mode: str = DECISION_MODE_RULE

    def as_dict(self) -> dict:
        return {
            "fill_basis": self.fill_basis,
            "adjust_mode": self.adjust_mode,
            "money_policy_version": self.money_policy_version,
            "engine_version": self.engine_version,
            "decision_mode": self.decision_mode,
        }

    def matches(self, other: "ExecutionFingerprint") -> bool:
        return isinstance(other, ExecutionFingerprint) and self.as_dict() == other.as_dict()

    def difference(self, other: "ExecutionFingerprint") -> dict:
        """列出不一致的字段，供"不可比"的提示直接展示。"""
        if not isinstance(other, ExecutionFingerprint):
            return {"error": "not_a_fingerprint"}
        mine, theirs = self.as_dict(), other.as_dict()
        return {key: {"this": mine[key], "other": theirs[key]}
                for key in mine if mine[key] != theirs.get(key)}


def compare_allowed(a: ExecutionFingerprint, b: ExecutionFingerprint) -> tuple[bool, str]:
    """两份结果能不能对比。返回 (能否, 不能的原因)。"""
    if not isinstance(a, ExecutionFingerprint) or not isinstance(b, ExecutionFingerprint):
        return False, "缺少执行指纹，无法确认口径是否一致"
    diff = a.difference(b)
    if diff:
        detail = "、".join(f"{key}({value['this']} vs {value['other']})"
                           for key, value in diff.items())
        return False, f"口径不同，不可比：{detail}"
    return True, ""


_ENGINE_SOURCE = Path(__file__).with_name("strategy_engine.py")


@lru_cache(maxsize=1)
def engine_version() -> str:
    """引擎指纹：由**代码与常量**推导，而不是人工维护的字符串。

    为什么这么做："记得改版本号"这件事一定会忘。而引擎一改（或契约常量一改），
    历史痕迹的解释依据就变了 —— 把依据本身算成哈希，忘不了。
    """
    digest = hashlib.sha256()
    try:
        digest.update(_ENGINE_SOURCE.read_bytes())
    except OSError:  # pragma: no cover —— 源码缺失时退化成只看常量
        digest.update(b"engine-source-unavailable")
    for group in (DECISIONS, SETTLEMENT_KINDS, FILL_BASES, ADJUST_MODES, SKIP_REASONS):
        digest.update("|".join(group).encode("utf-8"))
    digest.update(str(MONEY.version).encode("utf-8"))
    digest.update(str(SNAPSHOT_SCHEMA_VERSION).encode("utf-8"))
    return digest.hexdigest()[:12]


def reset_engine_version_cache() -> None:
    """测试用：常量被 monkeypatch 之后需要重算。"""
    engine_version.cache_clear()


def fingerprint_for(settlement_kind: Any, *, adjust_mode: Any = ADJUST_NONE,
                    engine: Optional[str] = None,
                    decision_mode: Any = DECISION_MODE_RULE) -> ExecutionFingerprint:
    """按结算类型构造指纹。

    `adjust_mode` / `decision_mode` 都由调用方**声明**（它才知道这批数字是怎么来的）：
    模拟盘默认不复权；决策来源默认 rule，agent 袖套必须显式声明成 agent ——
    否则两段曲线会被判成可比。
    """
    return ExecutionFingerprint(
        fill_basis=fill_basis_for(settlement_kind),
        adjust_mode=normalize_adjust_mode(adjust_mode),
        money_policy_version=MONEY.version,
        engine_version=engine or engine_version(),
        decision_mode=normalize_decision_mode(decision_mode),
    )


# ==================== 快照 ====================

def indicator_keys_for(config: Any) -> list[str]:
    """这份策略**实际用到**的指标键（不写死"固定那几个 MA"）。

    这就是"不写死"的落点：以后加条件类型，只在这里加一条映射，
    `evaluate-bar` 的快照与痕迹都不用动。
    """
    keys = {"close"}
    if not isinstance(config, dict):
        return sorted(keys)
    for group in ("entry", "exit"):
        rule = config.get(group) if isinstance(config.get(group), dict) else {}
        for condition in rule.get("conditions") or ():
            if not isinstance(condition, dict):
                continue
            ctype = str(condition.get("type") or "")
            if ctype == "ma_cross":
                for key in ("fast", "slow"):
                    window = condition.get(key)
                    if isinstance(window, int) and window > 0:
                        keys.add(f"ma_{window}")
            elif ctype in ("price_cross_ma", "price_above_ma", "price_below_ma"):
                window = condition.get("window")
                if isinstance(window, int) and window > 0:
                    keys.add(f"ma_{window}")
            elif ctype in ("rsi_above", "rsi_below"):
                keys.add("rsi")
            elif ctype == "macd_cross":
                keys.update({"macd_dif", "macd_dea"})
    return sorted(keys)


def build_snapshot(*, bar: dict, indicators: dict, params: dict,
                   fingerprint: ExecutionFingerprint, extra: Optional[dict] = None) -> dict:
    """构造痕迹里的快照体。

    形状固定为 `SNAPSHOT_KEYS` + 自带 `schema_version`：以后加字段不必改历史行的解释逻辑
    （历史行说自己用的是哪一版）。
    """
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "bar": dict(bar or {}),
        "indicators": dict(indicators or {}),
        "params": dict(params or {}),
        "fingerprint": fingerprint.as_dict(),
        "extra": dict(extra or {}),
    }


def no_bar_facts(settlement_kind: Any, adjust_mode: Any = None,
                 decision_mode: Any = DECISION_MODE_RULE) -> dict:
    """没有 bar 时的执行事实：快照为 `None`，但**口径仍要写清楚**。

    为什么错误分支也要带它：调用方（Java 结算）拿到一个没有口径的错误响应时，
    只能猜"这条记录属于哪个口径"，或者干脆不记 —— 两者都会让"为什么今天没动"变回无解。
    """
    fingerprint = fingerprint_for(settlement_kind or "", adjust_mode=adjust_mode or "",
                                  decision_mode=decision_mode)
    return {
        "fill_basis": fingerprint.fill_basis,
        "fills": {"close": None, "next_open": None},
        "fingerprint": fingerprint.as_dict(),
        "snapshot": None,
    }


def describe() -> dict:
    """给 `/health` 与调试用：**当前生效**的口径与常量（不做运行时可变的配置）。"""
    return {
        "decisions": list(DECISIONS),
        "settlement_kinds": list(SETTLEMENT_KINDS),
        "fill_bases": list(FILL_BASES),
        "fill_basis_by_settlement": dict(FILL_BASIS_BY_SETTLEMENT),
        "adjust_modes": list(ADJUST_MODES),
        "decision_modes": list(DECISION_MODES),
        "skip_reasons": list(SKIP_REASONS),
        "unknown_prefix": UNKNOWN_PREFIX,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "money_policy": MONEY.describe(),
        "engine_version": engine_version(),
    }
