# -*- coding: utf-8 -*-
"""策略压力测试与诊断实验室（测评三/测评四，2026-10-17）。

三件事：
1) **情景分析**：合成单边暴涨/暴跌/高波动震荡等路径，把网格的两个"死穴"
   （上破卖飞、下破破网不断买入）显式摆出来——不是跑一遍总收益，而是回答
   "在什么行情下它输给买入持有、输多少、当时仓位是什么形态"。
2) **Walk-Forward 过拟合诊断**：样本内选参 → 样本外验证。IS 排名与 OOS 排名
   是否一致、IS 选出的参数在 OOS 处于什么百分位——过拟合的策略这两者必然分裂。
3) **PSI 环境监测**：开发期参考分布 vs 最近窗口分布的群体稳定性指数，
   超阈值给出"策略可能失效"预警（行业惯例阈值：0.1 / 0.25）。

外加测评四的两个探针：**确定性检查**（同输入多次运行，输出必须逐字节一致）与
**硬风控探针**（对抗性暴跌路径下，回撤止损必须由代码触发、触发后必须无买单）。

所有合成路径用固定 seed 的 Generator —— 压力测试本身必须可重复。
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable, Optional

import numpy as np

from agent import grid_strategy as gs

# ---------------------------------------------------------------------------
# 合成情景（OHLC 日线；open=昨收，high/low 包络当日开收）
# ---------------------------------------------------------------------------

#: 情景表：drift_pct=日漂移，amplitude_pct/period_bars=震荡分量，regime=分段切换
SCENARIOS: dict[str, dict] = {
    "trend_up":        {"label": "单边暴涨（+1%/日）", "drift_pct": 1.0},
    "trend_up_mild":   {"label": "温和上行（+0.4%/日）", "drift_pct": 0.4},
    "trend_down":      {"label": "单边下跌（-1.2%/日）", "drift_pct": -1.2},
    "crash":           {"label": "暴跌（前 40% 日 -3%，随后横盘）", "stages": [
        {"days_share": 0.4, "drift_pct": -3.0}, {"days_share": 0.6, "drift_pct": 0.0}]},
    "oscillate_high":  {"label": "高波动震荡（±4%，周期 10 日）",
                        "drift_pct": 0.0, "amplitude_pct": 4.0, "period_bars": 10},
    "oscillate_low":   {"label": "低波动震荡（±1%，周期 10 日）",
                        "drift_pct": 0.0, "amplitude_pct": 1.0, "period_bars": 10},
    "regime_shift":    {"label": "波动率切换（先低波后高波）", "stages": [
        {"days_share": 0.4, "drift_pct": 0.0, "amplitude_pct": 1.0, "period_bars": 8},
        {"days_share": 0.6, "drift_pct": 0.0, "amplitude_pct": 5.0, "period_bars": 8}]},
}


def synthetic_records(scenario: str | dict, days: int, start_price: float = 100.0,
                      seed: int = 7, noise_pct: float = 0.3) -> list[dict]:
    """按情景参数生成确定性 OHLC 日线。

    价格走**锚定目标路径**而不是复利随机游走：target_t = start × (1+drift)^t
    × (1 + amp×sin(2πt/period))，日收益 = target_t/target_{t-1} − 1 + 小噪声。
    为什么必须锚定：把噪声直接累乘进价格，"纯震荡"情景会因波动拖累+噪声游走
    漂成单边趋势（实测 80 天漂 -17%）——情景的定义就被破坏了，压力测试测的
    不再是你要的那个情景。锚定后震荡是**有界**的、趋势是**可控**的。
    open=昨收保证日收益路径可复算；high/low 留小包络（ATR 需要真实日内波幅）。
    """
    spec = SCENARIOS.get(scenario) if isinstance(scenario, str) else scenario
    if spec is None:
        raise ValueError(f"未知情景：{scenario}")
    stages = spec.get("stages") or [{"days_share": 1.0, **{k: v for k, v in spec.items()
                                                           if k in ("drift_pct", "amplitude_pct", "period_bars")}}]
    rng = np.random.default_rng(seed)
    records: list[dict] = []
    day_in_stage = 0
    anchor = float(start_price)      # 本阶段的锚（阶段切换时以当前 target 重锚）
    for stage in stages:
        stage_days = max(1, int(round(stage.get("days_share", 1.0) * days)))
        drift = float(stage.get("drift_pct", 0.0)) / 100.0
        amp = float(stage.get("amplitude_pct", 0.0)) / 100.0
        period = max(2, int(stage.get("period_bars", 10)))
        for j in range(stage_days):
            target = anchor * ((1.0 + drift) ** (j + 1)) * (1.0 + amp * np.sin(2 * np.pi * (day_in_stage % period) / period))
            # 噪声每天对 target 新鲜扰动、不携带历史（否则比率随机游走，震荡仍会漂移）
            c = target * (1.0 + rng.normal(0.0, noise_pct / 100.0))
            o = records[-1]["close"] if records else float(start_price)
            hi = max(o, c) * (1 + abs(rng.normal(0, 0.002)))
            lo = min(o, c) * (1 - abs(rng.normal(0, 0.002)))
            records.append({"date": f"s{len(records):04d}", "open": round(o, 4),
                            "close": round(c, 4), "high": round(hi, 4), "low": round(lo, 4),
                            "volume": 10000.0})
            day_in_stage += 1
        anchor = target if stage_days else anchor
    return records


# ---------------------------------------------------------------------------
# 情景矩阵：把"卖飞 / 破网"两个死穴做成可断言的标志位
# ---------------------------------------------------------------------------

def scenario_matrix(config: dict, days: int = 80, seed: int = 7,
                    scenarios: Optional[list[str]] = None,
                    start_price: float = 100.0) -> dict:
    """对每个合成情景跑一遍网格，输出表现对照 + 死穴标志。

    标志口径（全部来自引擎留痕，不重算行情）：
    - sold_out_in_uptrend（卖飞）：出现上破事件、期末零库存、且基准涨幅 > 10%
      ——货在最下面几格就卖完了，后面的涨幅与策略无关；
    - halted_buying_in_downtrend（破网停买）：出现下破事件且 halt 挡下的买单 > 0
      ——正是"不断破网买入"被硬规则拦住的证据；
    - risk_stop_fired：组合级回撤止损触发。
    """
    rows = []
    for name in (scenarios or list(SCENARIOS.keys())):
        spec = SCENARIOS[name]
        records = synthetic_records(name, days, start_price=start_price, seed=seed)
        result = gs.run_grid_backtest(config, records)
        if result.get("error"):
            rows.append({"scenario": name, "label": spec["label"], "error": result["error"]})
            continue
        events = [e["event"] for e in result.get("grid_events", [])]
        skips = result.get("skip_counter", {})
        bh = result.get("buy_and_hold_return_pct", 0.0)
        row = {
            "scenario": name, "label": spec["label"],
            "grid_return_pct": result.get("total_return_pct"),
            "buy_hold_return_pct": bh,
            "excess_return_pct": result.get("excess_return_pct"),
            "max_dd_pct": result.get("max_drawdown_pct"),
            "trade_count": result.get("trade_count"),
            "breakout_up_count": events.count("breakout_up"),
            "breakout_down_count": events.count("breakout_down"),
            "risk_stop_fired": bool(result.get("risk_stopped")),
            "sold_out_in_uptrend": (events.count("breakout_up") > 0
                                    and not result.get("open_inventory")
                                    and bh > 10),
            "halted_buying_in_downtrend": (events.count("breakout_down") > 0
                                           and skips.get(gs.SKIP_HALTED, 0) > 0),
            "diagnosis": None,
        }
        row["diagnosis"] = _scenario_diagnosis(row)
        rows.append(row)
    return {"days": days, "seed": seed, "rows": rows}


def _scenario_diagnosis(row: dict) -> str:
    if row.get("error"):
        return "该情景未能运行"
    parts = []
    if row.get("excess_return_pct") is not None and row["excess_return_pct"] < 0:
        parts.append(f"跑输买入持有 {abs(row['excess_return_pct'])}%")
    if row.get("sold_out_in_uptrend"):
        parts.append("上破后库存耗尽（卖飞）：涨幅大部分与策略无关")
    if row.get("halted_buying_in_downtrend"):
        parts.append("下破触发停买：硬规则拦住了'破网不断买入'")
    if row.get("risk_stop_fired"):
        parts.append("组合回撤止损被触发（确定性代码执行）")
    if not parts:
        parts.append("无死穴标志触发")
    return "；".join(parts)


# ---------------------------------------------------------------------------
# Walk-Forward 过拟合诊断
# ---------------------------------------------------------------------------

def walk_forward(config: dict, records: list[dict],
                 param_grid: Optional[dict[str, list]] = None,
                 segments: int = 3, metric: str = "total_return_pct") -> dict:
    """样本内选参、样本外验证：IS 最优参数在 OOS 还是不是最优？

    输出两个核心判据：
    - oos_percentile_of_picks：每折 IS 冠军在 OOS 的百分位（50=中游，越高越能迁移；
      长期低于 50 = IS 的"最优"是噪声拟合）；
    - is_oos_rank_corr：IS 排名与 OOS 排名的平均 Spearman 相关（>0 才谈得上稳定）。
    参数网格默认在 atr_multiplier 上扫——网格策略的第一过拟合重灾区。
    """
    grid = param_grid or {"atr_multiplier": [0.6, 0.8, 1.0, 1.2, 1.5]}
    cfg = gs.GridConfig.model_validate(config)
    min_bars = max(30, cfg.atr_window + 10)
    from agent.strategy_engine import split_segments
    split = split_segments(records, segments + 1, min_bars_per_segment=min_bars)
    segs = split["segments"]
    folds = len(segs) - 1
    if folds < 1:
        return {"error": f"数据不够切出 IS/OOS 折（每段至少 {min_bars} 根）",
                "segments_used": split["segments_used"], "note": split["note"]}

    param_names = list(grid.keys())
    combos = [dict(zip(param_names, vals))
              for vals in _cartesian([grid[k] for k in param_names])]
    fold_rows = []
    pick_percentiles = []
    rank_corrs = []
    for f in range(folds):
        is_seg, oos_seg = segs[f]["records"], segs[f + 1]["records"]
        is_scores, oos_scores = [], []
        for combo in combos:
            variant = {**config, **combo}
            is_r = gs.run_grid_backtest(variant, is_seg)
            oos_r = gs.run_grid_backtest(variant, oos_seg)
            is_scores.append(float(is_r.get(metric) or 0.0))
            oos_scores.append(float(oos_r.get(metric) or 0.0))
        pick = int(np.argmax(is_scores))
        oos_percentile = float(_percentile_of(oos_scores, pick))
        pick_percentiles.append(oos_percentile)
        rank_corrs.append(_spearman(is_scores, oos_scores))
        fold_rows.append({
            "fold": f + 1,
            "is_range": f"{segs[f]['start']}~{segs[f]['end']}",
            "oos_range": f"{segs[f + 1]['start']}~{segs[f + 1]['end']}",
            "picked": combos[pick],
            "is_score": round(is_scores[pick], 2),
            "oos_score_of_pick": round(oos_scores[pick], 2),
            "oos_best": round(max(oos_scores), 2),
            "oos_percentile_of_pick": round(oos_percentile, 1),
        })
    avg_percentile = float(np.mean(pick_percentiles))
    avg_corr = float(np.mean(rank_corrs)) if rank_corrs else None
    return {
        "param_grid": grid, "combos": len(combos), "folds": folds, "metric": metric,
        "rows": fold_rows,
        "oos_percentile_of_picks_mean": round(avg_percentile, 1),
        "is_oos_rank_corr_mean": round(avg_corr, 3) if avg_corr is not None else None,
        "note": ("IS 冠军在 OOS 平均百分位 < 50 且排名相关 ≤ 0：样本内的'最优参数'"
                 "基本是拟合噪声，参数应取中庸值而非调优值"
                 if avg_percentile < 50 and (avg_corr or 0) <= 0 else
                 "IS 排名对 OOS 有一定迁移性：参数选择不是纯过拟合"),
    }


def _cartesian(lists):
    if not lists:
        return [()]
    out = [[]]
    for lst in lists:
        out = [row + [v] for row in out for v in lst]
    return [tuple(r) for r in out]


def _percentile_of(scores: list[float], idx: int) -> float:
    """scores[idx] 在集合中的百分位（0=最差，100=最好）。"""
    v = scores[idx]
    below = sum(1 for s in scores if s < v)
    return below / (len(scores) - 1) * 100 if len(scores) > 1 else 50.0


def _spearman(a: list[float], b: list[float]) -> float:
    """Spearman 等级相关（小样本手工排名，避免引入 scipy 依赖）。"""
    if len(a) != len(b) or len(a) < 3:
        return 0.0
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    ra = (ra - ra.mean()) / (ra.std() or 1.0)
    rb = (rb - rb.mean()) / (rb.std() or 1.0)
    return float(np.mean(ra * rb))


# ---------------------------------------------------------------------------
# PSI（群体稳定性指数）环境监测
# ---------------------------------------------------------------------------

def psi(expected, actual, bins: int = 10) -> dict:
    """PSI = Σ (a% - e%) × ln(a% / e%)，分位箱由参考分布（expected）决定。

    行业惯例阈值：<0.1 稳定；0.1-0.25 轻度偏移（关注）；>0.25 显著偏移
    （策略开发环境与当前环境已不同，"策略可能失效"的预警线）。
    """
    e = np.asarray(expected, dtype=float)
    a = np.asarray(actual, dtype=float)
    e, a = e[np.isfinite(e)], a[np.isfinite(a)]
    if len(e) < 10 or len(a) < 10:
        return {"psi": None, "verdict": "insufficient_data",
                "note": "样本不足（两侧各至少 10 个观测）"}
    edges = np.quantile(e, np.linspace(0, 1, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    # 去重边界（参考分布大量重复值会让空箱/重箱失真）
    edges = np.unique(edges)
    e_counts = np.histogram(e, bins=edges)[0]
    a_counts = np.histogram(a, bins=edges)[0]
    epson = 1e-6
    e_pct = e_counts / len(e) + epson
    a_pct = a_counts / len(a) + epson
    value = float(np.sum((a_pct - e_pct) * np.log(a_pct / e_pct)))
    if value < 0.1:
        verdict, zh = "stable", "环境稳定：当前分布与开发期参考分布一致"
    elif value < 0.25:
        verdict, zh = "moderate", "轻度偏移：留意，策略表现可能开始漂移"
    else:
        verdict, zh = "shifted", "显著偏移：市场环境已不同于策略开发期，策略可能失效"
    return {"psi": round(value, 4), "verdict": verdict, "verdict_zh": zh,
            "bins": int(len(edges) - 1), "thresholds": {"stable": 0.1, "shifted": 0.25}}


def psi_records(reference_records: list[dict], recent_records: list[dict],
                feature: str = "returns") -> dict:
    """对行情算 PSI：默认用日收益率分布，可选 atr_pct（波动率环境）。

    参考期=策略开发时的样本，近期=要监测的窗口；两侧各自算特征后过 psi()。
    """
    def _feature(recs: list[dict]) -> np.ndarray:
        closes = np.array([float(r["close"]) for r in recs])
        if feature == "returns":
            return np.diff(closes) / closes[:-1]
        if feature == "atr_pct":
            highs = np.array([float(r["high"]) for r in recs])
            lows = np.array([float(r["low"]) for r in recs])
            return gs.compute_atr_pct(highs, lows, closes, 14)
        raise ValueError(f"不支持的特征：{feature}")

    out = psi(_feature(reference_records), _feature(recent_records))
    out["feature"] = feature
    out["reference_bars"] = len(reference_records)
    out["recent_bars"] = len(recent_records)
    return out


# ---------------------------------------------------------------------------
# 测评四：行为一致性 + 风控有效性探针
# ---------------------------------------------------------------------------

def determinism_check(config: dict, records: list[dict], runs: int = 3,
                      runner: Optional[Callable] = None) -> dict:
    """同一输入跑 N 遍：完整结果（含成交、净值、事件、跳过计数）的 sha256 必须一致。

    LLM 决策路径的"无理性动作翻转"靠封闭枚举 + 确定性解析压制；网格这条
    纯规则路径则必须做到**逐字节可复现**——这是它敢写进执行层的前提。
    """
    run = runner or gs.run_grid_backtest
    hashes = []
    for _ in range(max(2, runs)):
        result = run(config, records)
        payload = json.dumps(result, sort_keys=True, ensure_ascii=False, default=str)
        hashes.append(hashlib.sha256(payload.encode("utf-8")).hexdigest())
    identical = len(set(hashes)) == 1
    return {"runs": len(hashes), "identical": identical, "hashes": hashes,
            "note": "输出逐字节一致（确定性引擎）" if identical else
                    "同输入不同输出：存在非确定性来源（时序/浮点/随机数泄漏）"}


def risk_control_probe(config: dict, anchor_price: float = 100.0) -> dict:
    """对抗性暴跌路径下的硬风控探针（测评四的风控有效性）。

    路径构造：**先平静、后暴跌**（20 根 ±1% 震荡 + 25 根 -4%/日 + 15 根横盘）。
    为什么必须有平静段：网格要 atr_window+1 根才建网，直接从暴跌开始的话，
    大跌在预热期就走完了、建网时 ATR 已高企（网格极宽），部署浅得根本
    触不到止损线——那就不是在测风控，而是在测"网太宽"。平静段让网格
    按低波动收紧，随后的暴跌把每一格都砸穿，部署到底、回撤见真章。

    验证三件事：
    1) 组合回撤止损由**代码**触发（留痕 reason=risk_max_drawdown_stop）；
    2) 触发之后**再无任何买入**（不给"侥幸心理"留路径——这里根本没有模型）；
    3) 全程仓位上限从未被突破（equity_curve.position_pct ≤ max_position_pct）。
    """
    cfg = gs.GridConfig.model_validate(config)
    records = synthetic_records({"stages": [
        {"days_share": 0.33, "drift_pct": 0.0, "amplitude_pct": 1.0},
        {"days_share": 0.42, "drift_pct": -4.0},
        {"days_share": 0.25, "drift_pct": 0.0},
    ]}, days=60, start_price=anchor_price, seed=11)
    result = gs.run_grid_backtest(config, records)
    trades = result.get("trade_log", [])
    stop_trades = [t for t in trades if t.get("reason") == gs.REASON_RISK_STOP]
    stop_date = stop_trades[0]["date"] if stop_trades else None
    buys_after_stop = ([t for t in trades if t["side"] == "buy" and stop_date
                        and t["date"] > stop_date] if stop_date else [])
    max_position = max((row.get("position_pct") or 0.0) for row in result.get("equity_curve", [{}])) \
        if result.get("equity_curve") else 0.0
    return {
        "anchor_price": anchor_price,
        "risk_stop_fired": bool(stop_trades),
        "risk_stop_date": stop_date,
        "liquidated_shares": sum(t["shares"] for t in stop_trades),
        "buys_after_stop": len(buys_after_stop),
        "max_position_pct_seen": round(max_position, 2),
        "max_position_pct_cap": cfg.max_position_pct,
        "position_cap_held": max_position <= cfg.max_position_pct + 1e-6,
        "passed": bool(stop_trades) and not buys_after_stop
                  and max_position <= cfg.max_position_pct + 1e-6,
        "note": ("回撤止损触发、触发后零买入、仓位上限全程未破：硬风控由确定性代码执行"
                 if stop_trades and not buys_after_stop
                 and max_position <= cfg.max_position_pct + 1e-6 else
                 "硬风控探针未通过：见上方分项定位"),
    }
