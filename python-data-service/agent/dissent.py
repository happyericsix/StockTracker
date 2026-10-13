# -*- coding: utf-8 -*-
"""确定性空方简报（dissent_brief）：由代码从证据包算出的"反做多事实清单"。

<h3>为什么需要它（它和 bear 角色不是一回事）</h3>
委员会的反对意见来自同一个模型的另一次角色扮演：**相关性错误是共享的** ——
模型对最近行情有什么系统性偏差，bull 和 bear 就一起有什么偏差。这份简报
**不经过任何模型**：阈值、数字、结论全部由代码从证据包本身算出，bear 拿到的
是一份"不可协商的事实清单"。它不产生决策（那仍然是 risk_judge 的事），
只保证反对意见的**下限**：哪怕模型全体乐观，这些数字也在场。

<h3>纪律</h3>
1. **只陈述事实与数字，永不出现在"卖出"指令**：它给的是论据，不是结论；
2. **阈值是常量**：改任何一个都应当对着真实日线回放验证（与盯盘灵敏度同一条纪律）；
3. **纯函数**：同样的证据包永远得到同样的简报（可测、可归因）；
4. **只从证据包取数**：不另算指标、不另取数据 —— 证据包是 as-of 守卫过的，
   这里如果自己去取数，等于绕过了那道守卫。
"""
from __future__ import annotations

from typing import Any

#: 距 60 日高点的回撤深过这条线（%）才点名 —— 浅回撤是趋势里的正常呼吸。
DRAWDOWN_FACT_PCT = -8.0
#: 60 日收益为负且深过这条线（%）才点名。
NEG_RETURN_FACT_PCT = -5.0
#: RSI 过热线：这个位置上"新的做多"论据需要额外证据。
OVERBOUGHT_RSI = 70.0
#: 近 5 日均量 / 之前 15 日均量 低于它 → "价升量缩"（背离的量能口径）。
VOLUME_DIVERGENCE_RATIO = 0.7
#: 决策日"放量下跌"的量能倍数（对 20 日均量）。
DISTRIBUTION_VOLUME_RATIO = 1.5
#: 简报最多条数：它是下限保障不是清单大全，超过 6 条模型反而会略读。
MAX_FACTS = 6


def _f(value: Any) -> float | None:
    try:
        out = float(value)
        return out if out == out else None   # NaN → None
    except (TypeError, ValueError):
        return None


def bear_case_facts(evidence: dict) -> list[dict]:
    """从证据包（build_evidence 的产物）里找出**反对做多**的客观事实。

    返回 [{fact, detail}]（fact 是封闭的英文键，detail 是带数字的中文一句话）；
    没有任何事实命中时返回 []（调用方不注入空块）。
    """
    facts: list[dict] = []
    indicators = evidence.get("indicators") or {}
    derived = evidence.get("derived") or {}
    last_close = _f(evidence.get("last_close"))
    bars = evidence.get("recent_bars") or []
    position = evidence.get("position") or {}

    ma_20 = _f(indicators.get("ma_20"))
    ma_60 = _f(indicators.get("ma_60"))
    rsi = _f(indicators.get("rsi"))

    # ---- ① 均线空头排列：MA20 在 MA60 之下（趋势结构的反方事实） ----
    if ma_20 is not None and ma_60 is not None and ma_20 < ma_60:
        gap = (ma_20 / ma_60 - 1.0) * 100.0 if ma_60 else None
        facts.append({"fact": "death_alignment",
                      "detail": f"均线空头排列：MA20 {ma_20} 低于 MA60 {ma_60}"
                                + (f"（偏离 {gap:+.2f}%）" if gap is not None else "")})

    # ---- ② 价格在 MA60 之下：中长期趋势线下方 ----
    if last_close is not None and ma_60 is not None and last_close < ma_60:
        distance = (last_close / ma_60 - 1.0) * 100.0
        facts.append({"fact": "below_ma60",
                      "detail": f"现价 {last_close} 在 MA60 {ma_60} 之下（{distance:+.2f}%）"})

    # ---- ③ 距 60 日高点回撤过深 ----
    drawdown = _f(derived.get("distance_to_60d_high_pct"))
    if drawdown is not None and drawdown <= DRAWDOWN_FACT_PCT:
        facts.append({"fact": "drawdown_from_high",
                      "detail": f"现价距 60 日高点仍有 {drawdown:+.2f}% 的回撤未修复"})

    # ---- ④ 60 日收益为负 ----
    ret = _f(derived.get("return_60d_pct"))
    if ret is not None and ret <= NEG_RETURN_FACT_PCT:
        facts.append({"fact": "negative_60d_return",
                      "detail": f"近 60 日收益 {ret:+.2f}%（中期持有者整体被套方向）"})

    # ---- ⑤ RSI 过热：对"现在新开多仓"的反方事实 ----
    if rsi is not None and rsi >= OVERBOUGHT_RSI:
        facts.append({"fact": "overbought",
                      "detail": f"RSI {rsi} 已过 {OVERBOUGHT_RSI:.0f}（短线过热，追多的赔率变差）"})

    # ---- ⑥ 价升量缩（需要至少 20 根 bar 才判） ----
    if len(bars) >= 20 and last_close is not None:
        recent = bars[-5:]
        prior = bars[-20:-5]
        closes_recent = [_f(bar.get("close")) for bar in recent]
        closes_prior = [_f(bar.get("close")) for bar in prior]
        vols_recent = [_f(bar.get("volume")) for bar in recent]
        vols_prior = [_f(bar.get("volume")) for bar in prior]
        if (all(v is not None for v in closes_recent) and closes_prior
                and closes_prior[0] is not None
                and all(v is not None for v in vols_recent) and vols_prior
                and all(v is not None for v in vols_prior)):
            price_up = last_close > closes_prior[0]
            avg_recent = sum(vols_recent) / len(vols_recent)
            avg_prior = sum(vols_prior) / len(vols_prior)
            if (price_up and avg_prior > 0
                    and avg_recent / avg_prior < VOLUME_DIVERGENCE_RATIO):
                facts.append({
                    "fact": "price_volume_divergence",
                    "detail": (f"近 5 日价格上行但均量只有之前 15 日的 "
                               f"{avg_recent / avg_prior:.0%}（价升量缩）")})

    # ---- ⑦ 决策日放量下跌（派发口径） ----
    if bars:
        today = bars[-1]
        open_price, close_price = _f(today.get("open")), _f(today.get("close"))
        volume_ratio = _f(derived.get("volume_vs_20d_avg"))
        if (open_price is not None and close_price is not None
                and close_price < open_price and volume_ratio is not None
                and volume_ratio >= DISTRIBUTION_VOLUME_RATIO):
            facts.append({"fact": "distribution_day",
                          "detail": (f"当日收阴且量能是 20 日均量的 {volume_ratio:.1f} 倍"
                                     "（放量下跌）")})

    # ---- ⑧ 持仓浮亏：对"继续持有"的反方事实（与 trend 无关的账面事实） ----
    shares = _f(position.get("shares")) or 0.0
    avg_cost = _f(position.get("avg_cost"))
    if shares > 0 and avg_cost is not None and last_close is not None and last_close < avg_cost:
        loss = (last_close / avg_cost - 1.0) * 100.0
        facts.append({"fact": "underwater_position",
                      "detail": f"当前持仓 {shares} 股处于浮亏 {loss:+.2f}%（成本 {avg_cost}）"})

    return facts[:MAX_FACTS]


def brief_for(evidence: dict) -> dict | None:
    """把事实清单包成进证据包的那一块；没有任何事实时返回 None（不注入空块）。"""
    facts = bear_case_facts(evidence)
    if not facts:
        return None
    return {
        "note": ("以下是代码从行情与持仓确定性算出的**空方事实清单**（不经任何模型、不可协商）。"
                 "它不是结论、更不是指令：每一条都要么被你的论证正面回应，要么被承认。"
                 "熊市/回调环境里它条数多是正常的；一条都没有时说明当前证据不构成反方事实。"),
        "facts": facts,
    }
