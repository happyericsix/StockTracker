# -*- coding: utf-8 -*-
"""前日预案制（T+1 条件化交易预案）：委员会预注册的条件单，盘中确定性执行。

<h3>为什么要有预案</h3>
"同 bar 决策与成交"偏差的根治：委员会 T 日 15:30 用**完整**的当日数据开会，
产出的是**下一交易日**的条件化预案（if-then 计划），T+1 盘中由本模块的
确定性判定触发执行——决策时间严格先于成交时间，无未来函数。

<h3>与授权（authorization）的关系</h3>
授权是**边界**（布尔/比例：允不允许开仓、仓位上限），预案是边界内的
**条件化执行权**（什么价格/均线/量比条件下做什么）。预案不越过授权：
Java 结算侧执行预案单时仍被当日授权的 allow_open/allow_close/max_position_pct
夹取。授权是"能开多快"，预案是"什么时候踩油门"。

<h3>两条铁律</h3>
1. **零 LLM**：判定是封闭词表上的确定性谓词——预注册的意义就是"盘中不改主意"，
   盘中临场再问模型就回到了事后诸葛亮的路上。
2. **fail-open 归一**：模型输出的预案先过 `clean_plan`（词表外类型/越界参数
   丢弃或整块置 None），坏预案绝不挡结算，也绝不偷偷执行没写清楚的条件。

<h3>判定顺序：失效优先于触发</h3>
invalidation 命中（价格出区间/高影响新闻）→ 整个预案当日作废；
作废的预案不能再触发（"死了就不能开枪"）。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ==================== 封闭词表 ====================
#: 触发条件类型（entries/exits 共用）。price_* 用绝对价；ma_cross_* 用动态日线均线
#: （窗口随均线每日移动——写"触发价3.97"这种快照数字的事由 price_* 显式表达，
#: 动态均线条件必须用 ma_cross_*，不许把当前均线值抄成固定价）。
TRIGGER_PRICE_ABOVE = "price_above"
TRIGGER_PRICE_BELOW = "price_below"
TRIGGER_MA_CROSS_ABOVE = "ma_cross_above"
TRIGGER_MA_CROSS_BELOW = "ma_cross_below"
TRIGGER_TYPES = (
    TRIGGER_PRICE_ABOVE,
    TRIGGER_PRICE_BELOW,
    TRIGGER_MA_CROSS_ABOVE,
    TRIGGER_MA_CROSS_BELOW,
)

#: 失效条件类型 = 触发词表 + 新闻失效（高影响新闻 → 预案作废并恢复当日盯盘接管）。
INVALIDATION_NEWS_HIGH_IMPACT = "news_high_impact"
INVALIDATION_TYPES = TRIGGER_TYPES + (INVALIDATION_NEWS_HIGH_IMPACT,)

# ==================== 结构上限（防模型写小作文） ====================
MAX_ENTRIES = 2
MAX_EXITS = 2
MAX_INVALIDATIONS = 3
MIN_VALID_DAYS = 1
MAX_VALID_DAYS = 3
MAX_SUMMARY_CHARS = 200
DEFAULT_MA_WINDOW = 60          # ma_cross_* 没写窗口时的兜底（写入 warnings）
MIN_VOLUME_RATIO = 1.0          # require_volume_ratio 下限（≥1 才有"放量"语义）
MAX_VOLUME_RATIO = 10.0

#: 严格穿越的相对容差：现价须越过均线 0.1% 才算"上穿/跌破"，
#: 防止贴着均线的浮点抖动反复触发（与 intraday_watch 的 ±1% 容差同思想，量级更小——
#: 预案触发是执行级事件，要求比"值得看一眼"的事件更干净）。
CROSS_EPS = 0.001


def _f(value: Any) -> Optional[float]:
    """宽容取数：None/坏值 → None（与 intraday_watch._f 同款）。"""
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:  # NaN
        return None
    return parsed


# ==================== 归一（clean_plan） ====================

def _clean_trigger(raw: Any, warnings: list[str]) -> Optional[dict]:
    """单条触发条件：词表外类型 → None（丢弃该条，留警告）。"""
    if not isinstance(raw, dict):
        warnings.append("预案触发条件不是对象，已丢弃")
        return None
    kind = str(raw.get("type") or "").strip()
    if kind not in TRIGGER_TYPES:
        warnings.append(f"预案触发条件类型不在词表内（{kind!r}），已丢弃")
        return None

    trigger: dict = {"type": kind}

    if kind in (TRIGGER_PRICE_ABOVE, TRIGGER_PRICE_BELOW):
        value = _f(raw.get("value"))
        if value is None or value <= 0:
            warnings.append(f"{kind} 的 value 缺失或非正数，已丢弃该条")
            return None
        trigger["value"] = value

    if kind in (TRIGGER_MA_CROSS_ABOVE, TRIGGER_MA_CROSS_BELOW):
        window = raw.get("window")
        if window is None:
            warnings.append(f"{kind} 未写均线窗口，按默认 {DEFAULT_MA_WINDOW} 处理")
            trigger["window"] = DEFAULT_MA_WINDOW
        else:
            try:
                win = int(window)
            except (TypeError, ValueError):
                warnings.append(f"{kind} 的 window 不是整数（{window!r}），按默认 "
                                f"{DEFAULT_MA_WINDOW} 处理")
                win = DEFAULT_MA_WINDOW
            if win < 2 or win > 250:
                warnings.append(f"{kind} 的 window {win} 越界，已夹到 [2,250]")
                win = min(250, max(2, win))
            trigger["window"] = win

    ratio = raw.get("require_volume_ratio")
    if ratio is not None:
        value = _f(ratio)
        if value is None:
            warnings.append("require_volume_ratio 不是数字，忽略该叠加条件")
        else:
            clamped = min(MAX_VOLUME_RATIO, max(MIN_VOLUME_RATIO, value))
            if clamped != value:
                warnings.append(f"require_volume_ratio {value} 越界，已夹到 {clamped}")
            trigger["require_volume_ratio"] = clamped
    return trigger


def _clean_leg(raw: Any, side: str, warnings: list[str]) -> Optional[dict]:
    """单条 entry/exit：触发条件坏 → 丢弃整条（半截条件的单不能执行）。"""
    if not isinstance(raw, dict):
        warnings.append(f"预案 {side} 条目不是对象，已丢弃")
        return None
    leg_side = str(raw.get("side") or "").strip().lower()
    if leg_side and leg_side != side:
        warnings.append(f"预案 {side} 条目写了 side={leg_side!r}，已丢弃")
        return None
    trigger = _clean_trigger(raw.get("trigger"), warnings)
    if trigger is None:
        return None

    size = _f(raw.get("size_fraction"))
    if size is None or size <= 0.0:
        warnings.append(f"预案 {side} 的 size_fraction 缺失或 ≤0，已丢弃该条")
        return None
    if size > 1.0:
        warnings.append(f"预案 {side} 的 size_fraction {size} 越界，已夹到 1.0")
        size = 1.0

    leg: dict = {"side": side, "trigger": trigger, "size_fraction": round(size, 4)}

    price_range = raw.get("valid_price_range")
    if isinstance(price_range, (list, tuple)) and len(price_range) == 2:
        low, high = _f(price_range[0]), _f(price_range[1])
        if low is not None and high is not None and 0 < low < high:
            leg["valid_price_range"] = [low, high]
        else:
            warnings.append("valid_price_range 无效（须 [低,高] 且均为正数），已丢弃该字段")
    elif price_range is not None:
        warnings.append("valid_price_range 格式不对（须两项数组），已丢弃该字段")
    return leg


def _clean_invalidation(raw: Any, warnings: list[str]) -> Optional[dict]:
    if not isinstance(raw, dict):
        warnings.append("预案失效条件不是对象，已丢弃")
        return None
    kind = str(raw.get("type") or "").strip()
    if kind not in INVALIDATION_TYPES:
        warnings.append(f"预案失效条件类型不在词表内（{kind!r}），已丢弃")
        return None
    if kind == INVALIDATION_NEWS_HIGH_IMPACT:
        return {"type": kind}
    value = _f(raw.get("value"))
    if value is None or value <= 0:
        warnings.append(f"失效条件 {kind} 的 value 缺失或非正数，已丢弃")
        return None
    return {"type": kind, "value": value}


def clean_plan(raw: Any, warnings: list[str]) -> Optional[dict]:
    """把 JSON 块里的 plan 归一成可落库、可执行的预案（fail-open）。

    - 整块缺失/不是对象 → None（没写预案 = 沿用现状：盘中授权边界内执行）；
    - 每条 entry/exit/invalidation 独立归一，坏的丢弃、好的保留；
    - 全部条目都坏 → 保留空预案（"明天什么都不做"也是一份明确的预案，
      比静默没有预案更诚实——用户至少能看到委员会想过明天）。
    """
    if not isinstance(raw, dict):
        if raw is not None:
            warnings.append("plan 不是对象，按无预案处理")
        return None

    plan: dict = {}

    valid_days = raw.get("valid_days", MIN_VALID_DAYS)
    try:
        days = int(valid_days)
    except (TypeError, ValueError):
        warnings.append(f"valid_days 不是整数（{valid_days!r}），按 {MIN_VALID_DAYS} 处理")
        days = MIN_VALID_DAYS
    if days < MIN_VALID_DAYS or days > MAX_VALID_DAYS:
        clamped = min(MAX_VALID_DAYS, max(MIN_VALID_DAYS, days))
        warnings.append(f"valid_days {days} 越界，已夹到 {clamped}")
        days = clamped
    plan["valid_days"] = days

    summary = raw.get("summary")
    plan["summary"] = str(summary).strip()[:MAX_SUMMARY_CHARS] if summary else ""

    entries = []
    raw_entries = raw.get("entries") if isinstance(raw.get("entries"), list) else []
    if len(raw_entries) > MAX_ENTRIES:
        warnings.append(f"entries 超过 {MAX_ENTRIES} 条，只保留前 {MAX_ENTRIES} 条")
        raw_entries = raw_entries[:MAX_ENTRIES]
    for item in raw_entries:
        leg = _clean_leg(item, "buy", warnings)
        if leg:
            entries.append(leg)
    plan["entries"] = entries

    exits = []
    raw_exits = raw.get("exits") if isinstance(raw.get("exits"), list) else []
    if len(raw_exits) > MAX_EXITS:
        warnings.append(f"exits 超过 {MAX_EXITS} 条，只保留前 {MAX_EXITS} 条")
        raw_exits = raw_exits[:MAX_EXITS]
    for item in raw_exits:
        leg = _clean_leg(item, "sell", warnings)
        if leg:
            exits.append(leg)
    plan["exits"] = exits

    invalidation = []
    raw_inv = raw.get("invalidation") if isinstance(raw.get("invalidation"), list) else []
    if len(raw_inv) > MAX_INVALIDATIONS:
        warnings.append(f"invalidation 超过 {MAX_INVALIDATIONS} 条，只保留前 {MAX_INVALIDATIONS} 条")
        raw_inv = raw_inv[:MAX_INVALIDATIONS]
    for item in raw_inv:
        cleaned = _clean_invalidation(item, warnings)
        if cleaned:
            invalidation.append(cleaned)
    plan["invalidation"] = invalidation
    return plan


# ==================== 判定（evaluate_plan，纯函数、零 LLM） ====================

def _completed_closes(day_records: list[dict], today: str) -> list[float]:
    """完结日线的收盘序列（剔除今日未收盘 bar——不能拿半根 K 线算"昨天在哪侧"）。"""
    closes = []
    for record in day_records or []:
        if str(record.get("date", ""))[:10] == today:
            continue
        value = _f(record.get("close"))
        if value is not None:
            closes.append(value)
    return closes


def _today_volume(day_records: list[dict], today: str) -> Optional[float]:
    """今日累计成交量（数据源盘中的今日 bar 是运行值）；取不到 → None。"""
    for record in day_records or []:
        if str(record.get("date", ""))[:10] == today:
            return _f(record.get("volume"))
    return None


def _volume_ratio(day_records: list[dict], today: str) -> Optional[float]:
    """日线口径量比：今日累计量 / 近 5 个完结日均量（盘中单调递增，可触发即触发）。"""
    volumes = []
    for record in day_records or []:
        if str(record.get("date", ""))[:10] == today:
            continue
        value = _f(record.get("volume"))
        if value is not None:
            volumes.append(value)
    if len(volumes) < 5 or sum(volumes[-5:]) <= 0:
        return None
    today_vol = _today_volume(day_records, today)
    if today_vol is None:
        return None
    return today_vol / (sum(volumes[-5:]) / 5.0)


def _volume_ok(trigger: dict, day_records: list[dict], today: str) -> tuple[bool, str]:
    """require_volume_ratio 叠加条件：不满足时返回 (False, 原因)——这是"还没触发"
    而不是"失效"，量能随盘中累计上升，晚些时候仍可能满足。"""
    required = trigger.get("require_volume_ratio")
    if required is None:
        return True, ""
    ratio = _volume_ratio(day_records, today)
    if ratio is None:
        return False, f"量比算不出（数据不足），叠加条件未满足"
    if ratio < float(required):
        return False, f"量比 {ratio:.2f} < 要求 {float(required):.2f}，叠加条件未满足"
    return True, ""


def _ma_cross_hit(kind: str, window: int, price: float,
                  closes: list[float]) -> tuple[bool, str]:
    """盘中穿越日线均线的判定。

    昨收在均线另一侧（严格，容差 CROSS_EPS 外）+ 现价越到本侧（严格）→ 事件成立。
    今日均线用"window-1 根完结收盘 + 现价"近似（与收盘结算的正式均线只差最后一根
    的盘中演化——上穿条件自洽：现价越高今日均线也越高，判定偏保守而非激进）。
    """
    if len(closes) < window:
        return False, f"完结日线不足 {window} 根，均线条件无法判定"
    prev_close = closes[-1]
    ma_yesterday = sum(closes[-window:]) / window
    ma_now = (sum(closes[-(window - 1):]) + price) / window
    if kind == TRIGGER_MA_CROSS_ABOVE:
        below_prev = prev_close < ma_yesterday * (1.0 - CROSS_EPS)
        above_now = price > ma_now * (1.0 + CROSS_EPS)
        if below_prev and above_now:
            return True, f"昨收 {prev_close:.3f} 在 MA{window}({ma_yesterday:.4f}) 下方，" \
                         f"现价 {price:.3f} 已站上盘中均线 {ma_now:.4f}"
        return False, f"未构成上穿 MA{window}（盘中均线 {ma_now:.4f}，昨收 {prev_close:.3f}）"
    below_now = price < ma_now * (1.0 - CROSS_EPS)
    above_prev = prev_close > ma_yesterday * (1.0 + CROSS_EPS)
    if above_prev and below_now:
        return True, f"昨收 {prev_close:.3f} 在 MA{window}({ma_yesterday:.4f}) 上方，" \
                     f"现价 {price:.3f} 已跌破盘中均线 {ma_now:.4f}"
    return False, f"未构成跌破 MA{window}（盘中均线 {ma_now:.4f}，昨收 {prev_close:.3f}）"


def _trigger_hit(trigger: dict, price: float,
                 closes: list[float]) -> tuple[bool, str]:
    """单个触发条件的价格/均线判定（不含量比叠加）。"""
    kind = trigger.get("type")
    if kind in (TRIGGER_PRICE_ABOVE, TRIGGER_PRICE_BELOW):
        value = float(trigger["value"])
        if kind == TRIGGER_PRICE_ABOVE:
            if price >= value:
                return True, f"现价 {price:.3f} ≥ 触发价 {value:.3f}"
            return False, f"现价 {price:.3f} < 触发价 {value:.3f}"
        if price <= value:
            return True, f"现价 {price:.3f} ≤ 触发价 {value:.3f}"
        return False, f"现价 {price:.3f} > 触发价 {value:.3f}"
    return _ma_cross_hit(str(kind), int(trigger.get("window", DEFAULT_MA_WINDOW)),
                         price, closes)


def _invalidate_reason(plan: dict, price: float,
                       news_high_impact: bool) -> tuple[Optional[str], Optional[str]]:
    """失效判定：任一命中 → 当日预案作废。返回 (人话原因, 机器类型)。

    机器类型（invalidated_type）给结算侧区分"新闻失效（恢复盯盘接管）"与
    "价格失效（既有路径）"——不能拿中文串匹配做控制流。
    """
    for condition in plan.get("invalidation") or []:
        kind = condition.get("type")
        if kind == INVALIDATION_NEWS_HIGH_IMPACT:
            if news_high_impact:
                return ("出现高影响新闻，预案冻结（当日恢复盯盘接管）",
                        INVALIDATION_NEWS_HIGH_IMPACT)
            continue
        value = _f(condition.get("value"))
        if value is None:
            continue
        if kind == TRIGGER_PRICE_ABOVE and price >= value:
            return (f"现价 {price:.3f} ≥ 失效价 {value:.3f}（向上跳空出预案区间）",
                    TRIGGER_PRICE_ABOVE)
        if kind == TRIGGER_PRICE_BELOW and price <= value:
            return (f"现价 {price:.3f} ≤ 失效价 {value:.3f}（跌破预案失效线）",
                    TRIGGER_PRICE_BELOW)
    return None, None


def evaluate_plan(plan: Optional[dict], *, price: Optional[float],
                  day_records: list[dict], position: Optional[dict],
                  today: str, news_high_impact: bool = False) -> dict:
    """判定一份预案在当前时点的状态。**确定性、零 LLM、永不抛异常**。

    返回（形状稳定，Java 结算侧按它执行/留痕）：
        {"action": "buy|sell|hold", "matched": {...}|None, "invalidated": str|None,
         "invalidated_type": str|None, "size_fraction": 0.0-1.0,
         "price": float|None, "detail": str}

    判定顺序：失效 → 入场（仅空仓）→ 出场（仅持仓）。失效优先于一切。
    """
    def result(action: str, matched=None, invalidated=None, invalidated_type=None,
               size: float = 0.0, detail: str = "") -> dict:
        return {"action": action, "matched": matched, "invalidated": invalidated,
                "invalidated_type": invalidated_type,
                "size_fraction": round(size, 4), "price": price, "detail": detail}

    if not isinstance(plan, dict) or not plan:
        return result("hold", detail="无预案")
    if price is None or price <= 0:
        return result("hold", detail="无有效现价，本次不判定（等下一次轮询）")

    invalidated, invalidated_type = _invalidate_reason(plan, price, news_high_impact)
    if invalidated:
        return result("hold", invalidated=invalidated,
                      invalidated_type=invalidated_type, detail=invalidated)

    closes = _completed_closes(day_records, today)
    shares = _f((position or {}).get("shares")) or 0.0

    # 空仓 → 只评估 entries；持仓 → 只评估 exits（与 evaluate_bar 同纪律：
    # 不存在"已持仓还按入场条件加仓"的歧义路径）。
    legs = plan.get("entries") if shares <= 0 else plan.get("exits")
    side = "buy" if shares <= 0 else "sell"
    not_met: list[str] = []
    for leg in legs or []:
        trigger = leg.get("trigger") or {}
        hit, why = _trigger_hit(trigger, price, closes)
        if not hit:
            not_met.append(why)
            continue
        vol_ok, vol_why = _volume_ok(trigger, day_records, today)
        if not vol_ok:
            # 价格/均线已到位但量能没跟上：留 detail 不触发（下一根可能满足）。
            return result("hold", detail=f"{side} 触发条件部分满足：{why}；{vol_why}")
        price_range = leg.get("valid_price_range")
        if price_range and not (price_range[0] <= price <= price_range[1]):
            reason = (f"触发时现价 {price:.3f} 超出预案有效区间 "
                      f"[{price_range[0]:.3f}, {price_range[1]:.3f}]（跳空异常，按失效处理）")
            return result("hold", invalidated=reason, invalidated_type="out_of_range",
                          detail=reason)
        return result(side, matched=leg, size=float(leg.get("size_fraction", 0.0)),
                      detail=f"{why}")

    if not_met:
        return result("hold", detail="；".join(not_met))
    return result("hold", detail="预案生效中，触发条件未满足" if legs
                  else f"预案无{'入场' if shares <= 0 else '出场'}条目（纯观望预案）")


def describe() -> dict:
    """给 /health 与调试用：词表与上限。"""
    return {
        "trigger_types": list(TRIGGER_TYPES),
        "invalidation_types": list(INVALIDATION_TYPES),
        "limits": {"entries": MAX_ENTRIES, "exits": MAX_EXITS,
                   "invalidation": MAX_INVALIDATIONS,
                   "valid_days": [MIN_VALID_DAYS, MAX_VALID_DAYS]},
        "cross_eps": CROSS_EPS,
    }
