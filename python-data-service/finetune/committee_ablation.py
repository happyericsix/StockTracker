# -*- coding: utf-8 -*-
"""committee_ablation.py —— 委员会消融：同一行情、同一门控、同一账本，只变角色子集。

<h3>它要回答的问题</h3>
一轮全委会 = 8 次串行 LLM 调用 ≈ 21k tokens，其中 7 次花在"给裁决者铺垫语境"上。
这个形状是从 TradingAgents 借来的，**借来之后没有度量过**：前七棒到底有没有让
第八棒的决策更好？这个问题只有实验能回答：

```
judge  = 只有 risk_judge（1 次调用）         —— 语境铺垫价值 = full8 与它的差
duo    = bull → bear → judge（3 次调用）     —— 多空对抗的最小形态
core4  = bull → bear → trader → judge（4 次）—— 加上交易员提案
full8  = 全委会（8 次，生产形态）
```

<h3>口径纪律（与 evolve_replay 同源）</h3>
- **as-of 守卫内建**：证据只到当日（build_evidence 保证）；
- **门控相同**：四个变体走同一套 convene_reasons（同一触发才会开会，成本对比才有意义）；
- **会议上下文相同**：why_convened / strategy_intent / dissent_brief 用生产同一段
  （attach_meeting_context），变的只有角色子集；
- **判读相同**：5 日后回看方向对齐（与 DecisionQualityService 的对账口径一致）；
- **账本相同**：简化账本（replay_factory），硬止损硬通道先于委员会（镜像生产）。

<h3>诚实边界</h3>
- 简化账本（整手+滑点；无涨跌停/印花税/盘中），与 evolve_replay 同一口径；
- 单标的单时段的结果**不是结论**：它是一个可复跑的度量工具，结论要多标的/多时段
  汇总后才能下（跑法见 README/编年史）；
- token 计量来自注入 completion 的 usage（真跑时是真实用量）。

用法（python-data-service/ 目录）：
    python finetune/committee_ablation.py --symbol 601988 --start 2025-01-01 \
        --end 2025-06-30 --out finetune/ablation --variants judge,duo,core4,full8
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import trading_committee as tc  # noqa: E402
from finetune.replay_factory import (  # noqa: E402
    INITIAL_CASH, _apply_decision, _apply_hard_stop, _forward_return,
    _load_dotenv, _stop_pct_of, _trading_days,
)

# ==================== 变体（封闭集：消融实验要可比，形状必须固定） ====================
# 顺序一律按 ROLES 的固定顺序（run_committee 保证）；决策永远只认 risk_judge。
VARIANTS = {
    "judge": ("risk_judge",),
    "duo": ("bull", "bear", "risk_judge"),
    "core4": ("bull", "bear", "trader", "risk_judge"),
    "full8": tuple(tc.ROLES),
}

#: 判读窗口（交易日）：与生产 DecisionQualityService 的默认对账口径一致。
HORIZON_DAYS = 5

#: LLM 连续失败的中止线：与 evolve_replay 同一条纪律（402 被当普通失败
#: 跑出 18 天假 HOLD 的教训）—— 消融里一个变体烧穿了，其余变体的数字也别信。
MAX_CONSECUTIVE_LLM_FAILURES = 2


def run_variant(name: str, roles: tuple[str, ...], symbol: str, records: list[dict],
                start: str, end: str, config: dict, completion=None,
                budget_tokens: int = 0) -> dict:
    """跑一个变体：逐日门控 → 开会 → 记账 → 判读。返回该变体的报告。"""
    position = {"cash": INITIAL_CASH, "shares": 0, "avg_cost": None}
    last_decision_at = None
    report = {"variant": name, "roles": list(roles), "days": 0, "convened": 0,
              "skipped": 0, "hard_stop": 0, "decisions": {"buy": 0, "sell": 0, "hold": 0},
              "aligned": {"judged": 0, "wins": 0}, "llm_calls": 0, "tokens": 0,
              "equity_curve": [], "error": None}
    consecutive_failures = 0
    stop = False

    for as_of in _trading_days(records, start, end):
        if stop:
            break
        report["days"] += 1
        row = next(r for r in records if str(r.get("date")) == as_of)
        close = float(row.get("close") or 0.0)
        day_low = float(row.get("low") or 0.0) or None

        # 硬止损先于委员会（镜像生产"生存线不等模型"）
        if _apply_hard_stop(position, day_low, _stop_pct_of(config)) == "hard_stop":
            report["hard_stop"] += 1
            last_decision_at = as_of

        records_so_far = [r for r in records if str(r.get("date")) <= as_of]
        evidence, warnings = tc.build_evidence(symbol, as_of, records_so_far, position)
        if not evidence:
            continue

        reasons, gate = tc.convene_reasons(records_so_far, as_of, config=config,
                                           position=position,
                                           last_decision_at=last_decision_at)
        if not reasons:
            report["skipped"] += 1
            continue
        report["convened"] += 1

        # 会议上下文：与生产 decide() 同一段（变的是角色子集，不是证据包）
        tc.attach_meeting_context(evidence, gate, reasons, config)

        result = tc.run_committee(symbol, as_of, evidence, list(warnings),
                                  completion=completion, roles=roles)
        report["tokens"] += result.total_tokens
        report["llm_calls"] += result.llm_calls

        if result.llm_calls == 0:
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_LLM_FAILURES:
                report["error"] = ("LLM 连续调用失败，本变体已中止"
                                   "（检查 API 余额/服务状态；此前数据有效）")
                break
        else:
            consecutive_failures = 0

        decision = result.decision
        last_decision_at = as_of
        fwd = _forward_return(records, as_of, HORIZON_DAYS)
        if decision.decision in ("buy", "sell") and fwd is not None:
            report["aligned"]["judged"] += 1
            if (fwd if decision.decision == "buy" else -fwd) > 0:
                report["aligned"]["wins"] += 1

        action = _apply_decision(position, decision.as_dict(), close)
        label = {"open": "buy", "add": "buy", "sell": "sell",
                 "partial_sell": "sell"}.get(action, "hold")
        report["decisions"][label] += 1
        report["equity_curve"].append(
            {"date": as_of, "equity": position["cash"] + position["shares"] * close})

        if budget_tokens and report["tokens"] >= budget_tokens:
            report["stopped_at"] = "token_budget"
            stop = True

    curve = [p["equity"] for p in report["equity_curve"]]
    report["final_equity"] = round(curve[-1], 2) if curve else INITIAL_CASH
    report["return_pct"] = round((report["final_equity"] / INITIAL_CASH - 1.0) * 100, 2)
    judged = report["aligned"]["judged"]
    report["aligned"]["win_rate_pct"] = round(report["aligned"]["wins"] * 100.0 / judged, 1) \
        if judged else None
    report["tokens_per_convene"] = round(report["tokens"] / report["convened"], 0) \
        if report["convened"] else 0
    return report


def run_ablation(symbol: str, start: str, end: str, config: dict, out_dir: str,
                 variants: dict | None = None, completion=None,
                 budget_tokens: int = 0) -> dict:
    """跑全部变体并落盘（report.json + ablation.md 对比表）。"""
    os.makedirs(out_dir, exist_ok=True)
    selected = variants or VARIANTS
    fetch_end = (date.fromisoformat(end) + timedelta(days=30)).isoformat()
    from akshare_client import get_history
    records = get_history(symbol, "", fetch_end, "day") or []

    overall = {"symbol": symbol, "start": start, "end": end,
               "horizon_days": HORIZON_DAYS, "variants": [], "error": None}
    if not records:
        overall["error"] = f"没有取到 {symbol} 的历史数据"
        return overall

    lines = [f"# {symbol} 委员会消融 {start} ~ {end}",
             "", "同一行情、同一门控、同一账本；变的只有角色子集。",
             "", "| 变体 | 角色 | 开会 | BUY/SELL/HOLD | 判读胜率 | 收益 | tokens/次 |",
             "|---|---|---|---|---|---|---|"]
    for name, roles in selected.items():
        report = run_variant(name, roles, symbol, records, start, end, config,
                             completion=completion, budget_tokens=budget_tokens)
        overall["variants"].append(report)
        judged = report["aligned"]["judged"]
        win_rate = f"{report['aligned']['win_rate_pct']}% ({judged}笔)" if judged else "无判读样本"
        dec = report["decisions"]
        lines.append(f"| {name} | {len(roles)} | {report['convened']} | "
                     f"{dec['buy']}/{dec['sell']}/{dec['hold']} | {win_rate} | "
                     f"{report['return_pct']}% | {int(report['tokens_per_convene'])} |")
        if report["error"]:
            lines.append(f"  - ⚠ {name}: {report['error']}")

    lines += ["", "读法：full8 与 judge 的判读胜率差 = 语境铺垫的**毛价值**；",
              "拿它除以 7 倍的 token 成本，才是这套委员会形状的性价比。",
              "单标的单时段不是结论——多跑几个时段/标的再下判断。"]
    with open(os.path.join(out_dir, "ablation.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(os.path.join(out_dir, "report.json"), "w", encoding="utf-8") as f:
        json.dump(overall, f, ensure_ascii=False, indent=1)
    return overall


DEFAULT_CONFIG = {
    "schema_version": "1.0",
    "name": "消融基准（趋势跟随）",
    "symbol": "601988",
    "initial_capital": INITIAL_CASH,
    "data": {"period": "day", "lookback_days": 250},
    "position": {"type": "percent", "size_pct": 50},
    "entry": {"logic": "all", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "above"}]},
    "exit": {"logic": "any", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "below"},
        {"type": "stop_loss_pct", "value": -8}]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}


def main() -> None:
    parser = argparse.ArgumentParser(description="委员会消融（角色子集对比）")
    parser.add_argument("--symbol", default="601988")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--out", default="finetune/ablation")
    parser.add_argument("--config", help="基准 config JSON 文件（缺省用内置趋势跟随模板）")
    parser.add_argument("--variants", default="judge,duo,core4,full8",
                        help="逗号分隔，可选: " + ",".join(VARIANTS))
    parser.add_argument("--budget-tokens", type=int, default=0)
    args = parser.parse_args()

    _load_dotenv()
    cfg = DEFAULT_CONFIG
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["symbol"] = args.symbol
    names = [name.strip() for name in args.variants.split(",") if name.strip()]
    selected = {name: VARIANTS[name] for name in names if name in VARIANTS}
    if not selected:
        raise SystemExit(f"没有可用的变体（可选：{','.join(VARIANTS)}）")

    report = run_ablation(args.symbol, args.start, args.end, cfg, args.out,
                          variants=selected, budget_tokens=args.budget_tokens)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
