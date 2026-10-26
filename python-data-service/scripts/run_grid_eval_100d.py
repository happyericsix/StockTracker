# -*- coding: utf-8 -*-
"""百日评测 runner：把四项测评在**真实行情**上跑一遍，产出结构化报告。

用法（在 python-data-service 目录下）：
    .venv/Scripts/python.exe scripts/run_grid_eval_100d.py            # 默认 3 只样本股
    .venv/Scripts/python.exe scripts/run_grid_eval_100d.py 600519 300750
    .venv/Scripts/python.exe scripts/run_grid_eval_100d.py --bars 100 --out reports

评测窗口：**截止今天往前 100 个交易日**（今天不是交易日时自然落在最近交易日）。
更早的行情只作两件事：ATR 预热（不建网不交易）与 PSI 参考分布（开发期环境）。

四项测评的落点：
  ① 结构化策略     grid_strategy.describe_grid / grid_builds —— 基准价/格数/ATR 间距公式/
                   每格资金/破网策略，全部结构化输出与留痕
  ② 指标计算与解读 perf_metrics.compute_metrics + rolling + monte_carlo
  ③ 压力与诊断     stress_lab.scenario_matrix / walk_forward / psi_records
  ④ 一致性与风控   stress_lab.determinism_check / risk_control_probe
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agent import grid_strategy as gs           # noqa: E402
from agent import perf_metrics as pm            # noqa: E402
from agent import stress_lab as sl              # noqa: E402

#: 默认标的池：主板大盘蓝筹 / 创业板高波动（20cm）/ 主板中盘，三种波动画像
DEFAULT_SYMBOLS = ["600519", "300750", "002415"]

#: 评测窗口之外的参考历史（PSI 开发期分布 + ATR 预热）
REFERENCE_BARS = 160


def grid_config(symbol: str, first_price: float) -> dict:
    """自适应网格策略配置（结构化、可复核）。

    初始资金按一手成本自动抬高（≥ 12 手的预算）：茅台一手约 13 万，10 万本金
    连一格都买不起（引擎会如实留痕 grid_lot_too_expensive，但那样测评就变成
    测"买不起"而不是测策略了）。比例参数不随资金规模变化。
    """
    from agent import market_rules as mr
    lot_cost = first_price * mr.lot_for(symbol)
    capital = max(100000.0, ((lot_cost * 12 // 10000) + 1) * 10000)
    return {
        "schema_version": "1.0",
        "name": f"自适应网格-{symbol}",
        "symbol": symbol,
        "initial_capital": capital,
        "base_price_mode": "anchor",
        "grid_count": 5,
        "atr_window": 14,
        "atr_multiplier": 1.2,
        "per_grid_capital_pct": 12.0,
        "breakout_policy": "halt",
        "recenter_cooldown_bars": 3,
        "atr_review_bars": 10,
        "spacing_rebuild_change_pct": 30.0,
        "max_drawdown_stop_pct": 15.0,
        "max_position_pct": 70.0,
        "commission_pct": 0.025,
        "slippage_pct": 0.05,
    }


def fetch_history(symbol: str, bars_needed: int):
    from akshare_client import get_history
    total = bars_needed + REFERENCE_BARS
    records = get_history(symbol, count=total)
    if not records:
        return None, f"取数失败：{symbol}"
    if len(records) < bars_needed + 20:
        return None, f"数据不足：拿到 {len(records)} 根，需要 ≥ {bars_needed + 20}"
    return records, None


def evaluate_symbol(symbol: str, bars: int) -> tuple[dict, str | None]:
    records, err = fetch_history(symbol, bars)
    if err:
        return {}, err
    window = records[-bars:]
    reference = records[:-bars] or records[:60]
    start_date = window[0]["date"]
    config = grid_config(symbol, float(window[0]["close"]))

    # ① 策略生成（结构化参数随首次建网留痕）+ 窗口内回测
    result = gs.run_grid_backtest(config, records, start_date=start_date)
    if result.get("error"):
        return {}, f"{symbol} 回测失败：{result['error']}"

    spacing = (result.get("grid_builds") or [{}])[0].get("spacing_pct")
    metrics = pm.compute_metrics(result["equity_curve"], result["trade_log"],
                                 float(config["initial_capital"]), spacing_pct=spacing)
    rolling = pm.rolling_window_metrics(result["equity_curve"], window=20, step=5)
    rets = pm.daily_returns([row["equity"] for row in result["equity_curve"]])
    mc = pm.monte_carlo(rets, n_sims=2000, seed=20261017,
                        initial_capital=float(config["initial_capital"]))

    scenarios = sl.scenario_matrix(config, days=80, seed=7,
                                   start_price=float(window[0]["close"]))
    walk = sl.walk_forward(config, window,
                           param_grid={"atr_multiplier": [0.8, 1.0, 1.2, 1.5, 1.8]},
                           segments=3)
    psi_ret = sl.psi_records(reference, window, feature="returns")
    psi_atr = sl.psi_records(reference, window, feature="atr_pct")
    determinism = sl.determinism_check(config, records, runs=3)
    probe = sl.risk_control_probe(
        {**config, "per_grid_capital_pct": 20.0, "grid_count": 6,
         "max_drawdown_stop_pct": 12.0, "max_position_pct": 90.0},
        anchor_price=float(window[-1]["close"]))

    report = {
        "symbol": symbol,
        "window": {"bars": bars, "start": start_date, "end": window[-1]["date"],
                   "first_close": float(window[0]["close"]),
                   "last_close": float(window[-1]["close"])},
        "initial_capital": config["initial_capital"],
        "atr_multiplier": config["atr_multiplier"],
        "test1_structured_params": {
            "config_signature": result.get("config_signature"),
            "grid_builds": result.get("grid_builds"),
            "skip_counter": result.get("skip_counter"),
            "grid_events": result.get("grid_events"),
        },
        "test1_backtest": {k: result.get(k) for k in (
            "total_return_pct", "buy_and_hold_return_pct", "excess_return_pct",
            "max_drawdown_pct", "sharpe_ratio", "trade_count", "closed_trades",
            "final_value", "risk_stopped", "funding_note", "rules")},
        "test2_metrics": metrics,
        "test2_rolling": {"stability": rolling.get("stability"), "note": rolling.get("note"),
                          "windows": len(rolling.get("rows") or [])},
        "test2_monte_carlo": mc,
        "test3_scenarios": scenarios,
        "test3_walk_forward": walk,
        "test3_psi": {"returns": psi_ret, "atr_pct": psi_atr},
        "test4_determinism": determinism,
        "test4_risk_probe": probe,
        "trades_sample": result.get("trade_log", [])[:12],
    }
    return report, None


def write_markdown(reports: list[dict], out_dir: Path, bars: int) -> Path:
    lines = [
        "# 模拟盘 Agent 四项测评报告（自适应网格策略）",
        "",
        f"- 评测窗口：截止 {time.strftime('%Y-%m-%d')} 往前 **{bars} 个交易日**"
        f"（更早行情仅作 ATR 预热与 PSI 参考分布）",
        "- 执行口径：与生产回测同一契约 —— 次日开盘±滑点、整手、佣金下限 5 元、"
        "印花税/过户费按官方生效日期表、涨跌停挡单、T+1",
        "- 确定性：全部环节固定 seed；同输入重跑结果逐字节一致（测评四）",
        "",
    ]
    for r in reports:
        w = r["window"]
        bt = r["test1_backtest"]
        m = r["test2_metrics"]
        q = m["trade_quality"]
        dd = m["drawdown"]
        builds = r["test1_structured_params"]["grid_builds"]
        b0 = builds[0] if builds else {}
        lines += [
            f"## {r['symbol']}（{w['start']} ~ {w['end']}，{w['bars']} 根）",
            "",
            "### 测评一：结构化策略参数（非模糊描述）",
            f"- 基准价 {b0.get('base_price')}；单侧格数 {b0.get('grid_count_per_side')}；"
            f"间距公式 spacing% = atr_multiplier({r.get('atr_multiplier')}) × ATR% → "
            f"建网时 {b0.get('spacing_pct')}%",
            f"- 每格资金 {b0.get('per_grid_capital')} 元 ≈ {b0.get('per_grid_shares')} 股；"
            f"边界 [{b0.get('lower_boundary')}, {b0.get('upper_boundary')}]；"
            f"破网策略 {b0.get('breakout_policy')}；硬风控：回撤止损 {b0.get('max_drawdown_stop_pct')}% / "
            f"仓位上限 {b0.get('max_position_pct')}%",
            f"- 建网/重建 {len(builds)} 次；跳过留痕 {r['test1_structured_params']['skip_counter']}",
            "",
            "### 测评二：指标计算与解读",
            f"- 收益：总 {bt['total_return_pct']}%（基准 {bt['buy_and_hold_return_pct']}%，"
            f"超额 {bt['excess_return_pct']}%）；年化 {m['annualized_return_pct']}%",
            f"- 风险调整：Sharpe {m['sharpe_ratio']} / Sortino {m['sortino_ratio']} / "
            f"Calmar {m['calmar_ratio']}——{m['risk_adjusted_note']}",
            f"- 回撤：最深 {dd['max_dd_pct']}%（{dd['peak_date']} → {dd['trough_date']}），"
            f"恢复 {dd['recovery_bars_from_trough'] if dd['recovery_bars_from_trough'] is not None else '未恢复'}根；"
            f"当前回撤 {dd['current_dd_pct']}%；最长水下 {dd['longest_underwater_bars']} 根",
            f"- 交易质量：胜率 {q['win_rate_pct']}% / 盈亏比 {q['payoff_ratio']} / "
            f"盈利因子 {q['profit_factor']} / R-Expectancy {q['r_expectancy']}（{q['note']}）",
            f"- 滚动窗口：Sharpe CV {r['test2_rolling']['stability'] and r['test2_rolling']['stability']['sharpe_cv']}"
            f"（{r['test2_rolling']['note']}）",
            f"- 蒙特卡洛（2000 次）：收益 P5 {r['test2_monte_carlo']['final_return_pct']['p5']}% / "
            f"P50 {r['test2_monte_carlo']['final_return_pct']['p50']}% / "
            f"P95 {r['test2_monte_carlo']['final_return_pct']['p95']}%；亏损概率 "
            f"{r['test2_monte_carlo']['prob_loss_pct']}%",
            "",
            "### 测评三：压力测试与诊断",
            "| 情景 | 网格 | 买入持有 | 超额 | 诊断 |",
            "|---|---|---|---|---|",
        ]
        for row in r["test3_scenarios"]["rows"]:
            lines.append(
                f"| {row['label']} | {row.get('grid_return_pct')}% | "
                f"{row.get('buy_hold_return_pct')}% | {row.get('excess_return_pct')}% | "
                f"{row.get('diagnosis')} |")
        walk = r["test3_walk_forward"]
        lines += [
            "",
            f"- Walk-Forward：{walk.get('folds')} 折，IS 冠军 OOS 百分位均值 "
            f"{walk.get('oos_percentile_of_picks_mean')}，IS/OOS 排名相关 "
            f"{walk.get('is_oos_rank_corr_mean')}——{walk.get('note')}",
            f"- PSI（收益率分布）：{r['test3_psi']['returns']['psi']} → "
            f"{r['test3_psi']['returns']['verdict_zh']}",
            f"- PSI（波动率环境）：{r['test3_psi']['atr_pct']['psi']} → "
            f"{r['test3_psi']['atr_pct']['verdict_zh']}",
            "",
            "### 测评四：行为一致性与风控",
            f"- 确定性：{r['test4_determinism']['runs']} 次重跑输出哈希一致 = "
            f"{r['test4_determinism']['identical']}",
            f"- 硬风控探针（对抗性暴跌）：{'通过' if r['test4_risk_probe']['passed'] else '未通过'}"
            f"（止损触发 {r['test4_risk_probe']['risk_stop_fired']}、止损后买单 "
            f"{r['test4_risk_probe']['buys_after_stop']} 笔、仓位上限守住 "
            f"{r['test4_risk_probe']['position_cap_held']}）",
            "",
        ]
    md_path = out_dir / "report.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="百日四项测评 runner")
    parser.add_argument("symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--bars", type=int, default=100)
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    out_dir = Path(args.out) if args.out else ROOT / "reports" / f"grid_eval_{time.strftime('%Y%m%d_%H%M')}"
    out_dir.mkdir(parents=True, exist_ok=True)

    reports = []
    failures = []
    for symbol in args.symbols:
        print(f"[{symbol}] 取数 + 四项测评 ……", flush=True)
        report, err = evaluate_symbol(symbol, args.bars)
        if err:
            failures.append(err)
            print(f"[{symbol}] 失败：{err}", flush=True)
            continue
        (out_dir / f"{symbol}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        reports.append(report)
        bt = report["test1_backtest"]
        print(f"[{symbol}] 网格 {bt['total_return_pct']}% vs 持有 {bt['buy_and_hold_return_pct']}% | "
              f"Sharpe {report['test2_metrics']['sharpe_ratio']} | "
              f"PF {report['test2_metrics']['trade_quality']['profit_factor']} | "
              f"确定性 {report['test4_determinism']['identical']}",
              flush=True)

    if reports:
        md = write_markdown(reports, out_dir, args.bars)
        print(f"\n报告：{md}")
    if failures:
        print("失败项：", "; ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
