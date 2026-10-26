# -*- coding: utf-8 -*-
"""策略绩效指标套件（测评二：指标计算与解读，2026-10-17）。

<h3>为什么单独立模块</h3>
strategy_engine 里只有 Sharpe / 最大回撤 / 年化三个数——它们回答"赚了多少、颠不颠"，
回答不了**风险调整后到底值不值**（Sortino/Calmar）、**亏了多久才回本**（回撤恢复）、
**赚的钱是靠胜率还是靠赔率**（盈亏比/盈利因子/R-Expectancy）、以及**这条曲线稳不稳**
（滚动窗口 + 蒙特卡洛）。测评二的合格标准点名要这一整套，而且要求"能计算并解释"——
所以每个指标都带口径注释，汇总入口 `compute_metrics` 附中文解读字段，不只是吐数。

所有函数纯确定性：同样的输入永远同样的输出（蒙特卡洛用固定 seed 的 Generator），
这是"可重复评估"的底线。
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

TRADING_DAYS = 252


# ---------------------------------------------------------------------------
# 日收益与风险调整指标
# ---------------------------------------------------------------------------

def daily_returns(equity_values) -> np.ndarray:
    """相邻两期简单收益率（len = len-1；首期没有"上一期"）。"""
    values = np.asarray(equity_values, dtype=float)
    if len(values) < 2:
        return np.array([])
    return np.diff(values) / values[:-1]


def sharpe_ratio(equity_values, *, periods: int = TRADING_DAYS) -> float:
    """年化 Sharpe（rf=0，与 strategy_engine._sharpe_ratio 同口径，便于对账）。"""
    rets = daily_returns(equity_values)
    if len(rets) < 2:
        return 0.0
    std = float(np.std(rets))
    if std == 0:
        return 0.0
    return float(np.mean(rets) / std * math.sqrt(periods))


def sortino_ratio(equity_values, *, periods: int = TRADING_DAYS) -> float:
    """年化 Sortino：分母只数下行波动（收益率 < 0 的均方偏离）。

    网格策略的净值形态是"上台阶、下缓坡"——上行波动是利润来源，不该被当作风险扣分；
    这正是 Sortino 相对 Sharpe 的增量信息。无亏损日时分母为 0，给大而有限的哨兵值
    （保持排序可比，又不至于让 JSON 里出现 Infinity）。
    """
    rets = daily_returns(equity_values)
    if len(rets) < 2:
        return 0.0
    downside = rets[rets < 0]
    if len(downside) == 0:
        return 99.999
    downside_std = math.sqrt(float(np.mean(downside ** 2)))
    if downside_std == 0:
        return 99.999
    return float(np.mean(rets) / downside_std * math.sqrt(periods))


def annualized_return_pct(total_return: float, bars: int) -> float:
    if bars <= 0 or total_return <= -1:
        return 0.0
    years = bars / TRADING_DAYS
    return ((1 + total_return) ** (1 / years) - 1) * 100


def calmar_ratio(total_return: float, max_dd_pct: float | None, bars: int) -> Optional[float]:
    """Calmar = 年化收益 / |最大回撤|。回撤为 0 时无定义（返回 None，不猜 0）。

    ``max_dd_pct`` 接受正负两种记法（深度 20 或 -20 都指 20%）——perf_metrics
    的 max_drawdown_stats 给正深度，strategy_engine 的 _max_drawdown_pct 给负值，
    谁传进来都按 |·| 处理，不再因符号约定不同而悄悄变 None。
    """
    if max_dd_pct is None or max_dd_pct == 0 or bars <= 0:
        return None
    return round(annualized_return_pct(total_return, bars) / abs(max_dd_pct), 3)


# ---------------------------------------------------------------------------
# 回撤与恢复（测评二点名：当前回撤 + 恢复时间，不只是历史最深那一次）
# ---------------------------------------------------------------------------

def max_drawdown_stats(equity_values, dates=None) -> dict:
    """最大回撤全景：深度、峰谷位置、**恢复位置**（爬回前高）、最长水下时长、当前回撤。

    "恢复时间"记两个口径：
    - recovery_bars_from_trough：从谷底爬回前高用了几根 bar（修复力）；
    - 未恢复时它是 None，改看 underwater_bars_since_peak —— 离前高还有多远、
      已经耗了多久。"深度 + 时长"合起来才是回撤的真实代价。
    """
    values = [float(v) for v in equity_values]
    if len(values) < 2:
        return {"max_dd_pct": 0.0, "peak_index": None, "trough_index": None,
                "recovery_index": None, "recovery_bars_from_trough": None,
                "current_dd_pct": 0.0, "underwater_bars_since_peak": 0,
                "longest_underwater_bars": 0,
                "peak_date": None, "trough_date": None, "recovery_date": None}

    running_high = values[0]
    peak_idx = 0
    max_dd = 0.0
    dd_peak = dd_trough = 0
    dd_recovery: Optional[int] = None
    underwater_start: Optional[int] = None
    longest_underwater = 0

    for i, v in enumerate(values):
        if v > running_high:
            if underwater_start is not None:
                longest_underwater = max(longest_underwater, i - underwater_start)
                underwater_start = None
            running_high = v
            peak_idx = i
            if dd_recovery is None and max_dd > 0:
                dd_recovery = i          # 创新高 = 最深那次回撤正式修复
        dd = (running_high - v) / running_high * 100.0
        if dd > max_dd:
            max_dd = dd
            dd_peak, dd_trough = peak_idx, i
            dd_recovery = None           # 出现更深的回撤，恢复重新计时
        if dd > 0 and underwater_start is None:
            underwater_start = peak_idx

    if underwater_start is not None:
        longest_underwater = max(longest_underwater, len(values) - underwater_start)
    current_dd = (running_high - values[-1]) / running_high * 100.0

    def _d(idx):
        if dates is None or idx is None or idx >= len(dates):
            return None
        return dates[idx]

    return {
        "max_dd_pct": round(max_dd, 2),
        "peak_index": dd_peak, "trough_index": dd_trough, "recovery_index": dd_recovery,
        "recovery_bars_from_trough": (dd_recovery - dd_trough) if dd_recovery is not None else None,
        "current_dd_pct": round(current_dd, 2),
        "underwater_bars_since_peak": (len(values) - 1 - peak_idx) if current_dd > 0 else 0,
        "longest_underwater_bars": longest_underwater,
        "peak_date": _d(dd_peak), "trough_date": _d(dd_trough), "recovery_date": _d(dd_recovery),
    }


# ---------------------------------------------------------------------------
# 交易质量（盈亏比 / 盈利因子 / R-Expectancy）
# ---------------------------------------------------------------------------

def round_trips(trade_log: list[dict]) -> list[dict]:
    """把成交流水按 FIFO 配成回合。

    口径：买入成本 = amount + fee（费用进成本）；卖出净得 = amount - fee。
    一笔卖出若跨多笔买入（补仓后部分平仓），按吃到的股数拆成多个回合——
    盈亏比/期望这些统计对"回合"敏感，回合边界必须与真实配对一致。
    holding_bars = 出/入场日在流水中的序差（日线口径的持有天数；T+1 下至少为 1）。
    """
    date_seq: dict[str, int] = {}
    for t in trade_log:
        d = str(t.get("date"))
        if d not in date_seq:
            date_seq[d] = len(date_seq)

    open_lots: list[dict] = []   # {date, shares, cost}（cost 是剩余股数的摊后总成本）
    trips: list[dict] = []
    for t in trade_log:
        side = t.get("side")
        shares = float(t.get("shares") or 0)
        amount = float(t.get("amount") or 0)
        fee = float(t.get("fee") or 0)
        d = str(t.get("date"))
        if side == "buy":
            open_lots.append({"date": d, "shares": shares, "cost": amount + fee})
        elif side == "sell" and open_lots:
            proceeds_total = amount - fee
            need = shares
            while need > 1e-9 and open_lots:
                lot = open_lots[0]
                take = min(need, lot["shares"])
                cost_part = lot["cost"] * take / lot["shares"]
                proceeds_part = proceeds_total * take / shares
                trips.append({
                    "entry_date": lot["date"], "exit_date": d, "shares": round(take, 2),
                    "entry_cost": round(cost_part, 2),
                    "exit_proceeds": round(proceeds_part, 2),
                    "pnl": round(proceeds_part - cost_part, 2),
                    "holding_bars": date_seq.get(d, 0) - date_seq.get(lot["date"], 0),
                })
                lot["shares"] -= take
                lot["cost"] -= cost_part
                if lot["shares"] <= 1e-9:
                    open_lots.pop(0)
                need -= take
    return trips


def grid_risk_units(trips: list[dict], spacing_pct: float | None) -> Optional[list[float]]:
    """网格回合的初始风险 R：买入后跌一格的损失（entry_cost × spacing%）。

    R-Expectancy 的分母必须有业务含义，不是随便挑一个数：网格交易建仓时就
    隐含着"跌破下一格止损"的风险，一格的金额就是它天然的 R。
    """
    if not spacing_pct or spacing_pct <= 0:
        return None
    return [t["entry_cost"] * spacing_pct / 100.0 for t in trips]


def trade_quality(trips: list[dict], risk_units: list[float] | None = None) -> dict:
    """胜率 / 盈亏比 / 盈利因子 / R-Expectancy（回合口径，费用已含）。"""
    if not trips:
        return {"closed_trades": 0, "win_rate_pct": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "payoff_ratio": None, "profit_factor": None,
                "expectancy_yuan": 0.0, "r_expectancy": None,
                "note": "没有已平仓回合（网格全程未配对成交或零交易）"}
    pnls = [t["pnl"] for t in trips]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    avg_win = float(np.mean(wins)) if wins else 0.0
    avg_loss = float(np.mean(losses)) if losses else 0.0
    payoff = round(avg_win / abs(avg_loss), 3) if avg_loss < 0 else None
    profit_factor = (round(sum(wins) / abs(sum(losses)), 3)
                     if losses and sum(losses) < 0 else None)
    r_exp = None
    if risk_units and len(risk_units) == len(trips):
        mean_r = float(np.mean(risk_units))
        if mean_r > 0:
            r_exp = round(float(np.mean(pnls)) / mean_r, 3)
    return {
        "closed_trades": len(trips),
        "win_rate_pct": round(len(wins) / len(trips) * 100.0, 2),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "payoff_ratio": payoff,
        "profit_factor": profit_factor,
        "expectancy_yuan": round(float(np.mean(pnls)), 2),
        "r_expectancy": r_exp,
        "note": _quality_note(payoff, profit_factor, len(wins) / len(trips)),
    }


def _quality_note(payoff, profit_factor, win_share) -> str:
    """一句人话解读：这组交易靠什么赚钱（测评二的"解释"要求）。"""
    if profit_factor is None:
        return "无亏损回合：盈利因子无定义（样本内没有亏过，注意样本量）"
    if profit_factor >= 1.5 and win_share >= 0.5 and (payoff or 0) >= 1:
        return "胜率与赔率兼备：多数回合为正且单次盈利大于单次亏损"
    if profit_factor >= 1.0 and win_share >= 0.5:
        return ("高胜率、低赔率结构：多数回合小赚，靠胜率覆盖偶发大亏"
                if (payoff or 0) < 1 else
                "多数回合为正，盈利主要靠胜率维持")
    if profit_factor >= 1.0 and win_share < 0.5:
        return "低胜率、高赔率结构：靠少数大盈利覆盖多数小亏损（网格常见形态）"
    if profit_factor < 1.0:
        return "盈利因子 < 1：已平仓回合整体为负，费用或破网损失在吃掉格子利润"
    return "盈利因子大于 1 但结构一般：赚钱主要靠其中一类回合"


# ---------------------------------------------------------------------------
# 滚动窗口稳定性（测评二的稳健性检验之一）
# ---------------------------------------------------------------------------

def rolling_window_metrics(equity_curve: list[dict], window: int = 20, step: int = 5) -> dict:
    """滚动窗口下的指标稳定性：每个窗口算收益/Sharpe/回撤，再给稳定性摘要。

    全窗口一条 Sharpe 会掩盖"前半段靠运气、后半段失效"——滚动把时间轴摊开。
    稳定性用 Sharpe 的变异系数（CV=std/|mean|）量化：CV 越大，策略表现越依赖
    你恰好从哪一段开始。
    """
    rows = []
    values = [float(r["equity"]) for r in equity_curve]
    dates = [str(r.get("date")) for r in equity_curve]
    if window < 5:
        window = 5
    if step < 1:
        step = 1
    for start in range(0, max(0, len(values) - window + 1), step):
        seg = values[start:start + window]
        seg_dates = dates[start:start + window]
        ret_pct = (seg[-1] / seg[0] - 1) * 100 if seg[0] > 0 else 0.0
        rows.append({
            "start_date": seg_dates[0], "end_date": seg_dates[-1],
            "total_return_pct": round(ret_pct, 2),
            "sharpe": round(sharpe_ratio(seg), 3),
            "max_dd_pct": max_drawdown_stats(seg)["max_dd_pct"],
        })
    sharpes = [r["sharpe"] for r in rows]
    rets = [r["total_return_pct"] for r in rows]
    if not rows:
        return {"window": window, "step": step, "rows": [], "stability": None,
                "note": "数据不足一个窗口"}
    mean_sharpe = float(np.mean(sharpes))
    std_sharpe = float(np.std(sharpes))
    cv = round(std_sharpe / abs(mean_sharpe), 3) if abs(mean_sharpe) > 1e-9 else None
    positive_share = round(sum(1 for r in rets if r > 0) / len(rets) * 100, 1)
    return {
        "window": window, "step": step, "rows": rows,
        "stability": {
            "sharpe_mean": round(mean_sharpe, 3), "sharpe_std": round(std_sharpe, 3),
            "sharpe_cv": cv,
            "positive_window_share_pct": positive_share,
            "worst_window_dd_pct": round(min(r["max_dd_pct"] for r in rows), 2),
        },
        "note": ("Sharpe 变异系数 > 1：窗口间表现差异大，单一指标不可信"
                 if cv is not None and cv > 1 else
                 "窗口间表现相对稳定" if cv is not None else
                 "平均 Sharpe 接近 0，CV 无意义（看正收益窗口占比）"),
    }


# ---------------------------------------------------------------------------
# 蒙特卡洛（测评二的稳健性检验之二：收益分布，而不是一条路径）
# ---------------------------------------------------------------------------

def monte_carlo(returns, n_sims: int = 1000, seed: int = 42,
                initial_capital: float = 100000.0) -> dict:
    """对日收益率做 bootstrap 重排，给出**收益分布**而非单条路径。

    同一组日收益，不同的发生顺序 = 不同的回撤深度与心理压力——顺序本身是运气。
    固定 seed 的 Generator 保证可重复（评测的确定性要求）。
    """
    rets = np.asarray(returns, dtype=float)
    rets = rets[np.isfinite(rets)]
    if len(rets) < 5 or n_sims < 10:
        return {"n_sims": 0, "note": "样本太少，无意义"}
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(rets), size=(int(n_sims), len(rets)))
    paths = initial_capital * np.cumprod(1.0 + rets[idx], axis=1)
    finals = paths[:, -1]
    running_max = np.maximum.accumulate(paths, axis=1)
    dds = (paths - running_max) / running_max * 100.0
    max_dds = dds.min(axis=1)
    q = lambda arr, p: round(float(np.percentile(arr, p)), 2)  # noqa: E731
    total_ret = (finals / initial_capital - 1) * 100
    return {
        "n_sims": int(n_sims), "seed": seed, "bars": int(len(rets)),
        "final_return_pct": {"p5": q(total_ret, 5), "p25": q(total_ret, 25),
                             "p50": q(total_ret, 50), "p75": q(total_ret, 75),
                             "p95": q(total_ret, 95)},
        "prob_loss_pct": round(float(np.mean(finals < initial_capital)) * 100, 2),
        "max_dd_pct": {"p50": q(max_dds, 50), "p95": q(max_dds, 5)},
        "note": (f"5% 分位仍为正（{q(total_ret, 5)}%）时策略的赚钱能力对顺序不敏感；"
                 f"亏损概率 {round(float(np.mean(finals < initial_capital)) * 100, 2)}%"),
    }


# ---------------------------------------------------------------------------
# 汇总入口（一次算全，附解读）
# ---------------------------------------------------------------------------

def compute_metrics(equity_curve: list[dict], trade_log: list[dict],
                    initial_capital: float, *, spacing_pct: float | None = None) -> dict:
    """测评二的完整答卷：风险收益 + 回撤恢复 + 交易质量，一次算全。"""
    values = [float(r["equity"]) for r in equity_curve]
    dates = [str(r.get("date")) for r in equity_curve]
    total_return = (values[-1] / initial_capital - 1) if values and initial_capital > 0 else 0.0
    dd = max_drawdown_stats(values, dates)
    trips = round_trips(trade_log)
    quality = trade_quality(trips, grid_risk_units(trips, spacing_pct))
    sharpe = sharpe_ratio(values)
    sortino = sortino_ratio(values)
    return {
        "initial_capital": initial_capital,
        "final_equity": round(values[-1], 2) if values else None,
        "total_return_pct": round(total_return * 100, 2),
        "annualized_return_pct": round(annualized_return_pct(total_return, len(values)), 2),
        "sharpe_ratio": round(sharpe, 3),
        "sortino_ratio": round(sortino, 3),
        "calmar_ratio": calmar_ratio(total_return, dd["max_dd_pct"], len(values)),
        "risk_adjusted_note": ("Sortino 显著高于 Sharpe：波动主要来自上行（对网格是健康形态）"
                               if sortino > sharpe * 1.5 and sortino < 99 else
                               "上下行波动相当：收益在承担双向风险"),
        "drawdown": dd,
        "trade_quality": quality,
        "closed_trades": quality["closed_trades"],
    }
