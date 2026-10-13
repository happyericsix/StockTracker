# -*- coding: utf-8 -*-
"""evolve_replay.py —— 自进化回放：沙盘里看 agent 交易一年、每周改自己的策略。

<h3>形态（用户原话的落地）</h3>
"单独一个区域，从第一天开始一天天喂 2025 年的数据，看它一步步交易、
怎么提出改变策略。" —— 本模块就是那个区域：
- **一天天喂**：as-of 守卫（build_evidence 内建）保证委员会每天只看到截至当日
  的证据，晚于当日的 bar 会被剔除并留警告。它永远不知道明天。
- **一步步交易**：轨 A——委员会逐日决策，简化账本演化仓位。
- **每周改策略**：轨 B——周末反思本周战绩 → 提案新的完整策略 config →
  回测闸门（用截至当时的历史，新旧对比）→ 更优才应用，否则记录否决。
- **编年史**：chronicle.md 按天/周写成可读流水记，跑完像读日记一样复盘。

<h3>分段纪律（与自嗨的唯一区别）</h3>
freeze 日期之后进入**冻结段**：不再提案，轨 A 用最终 config 跑完。
进化段的好看数字不算数，冻结段的才算。

<h3>诚实边界</h3>
- 账本为简化执行（整手+滑点；无涨跌停/印花税/盘中）；
- 提案验证用 strategy_engine 回测（与线上 DSL 同一引擎）；
- 线上周反思（app.weekly_reflection_run）是生产版，这里是沙盘专用提示词，
  口径对齐（教训=可执行规则）但不共享代码路径。

用法（python-data-service/ 目录）：
    python finetune/evolve_replay.py --symbol 601988 --start 2025-01-01 \
        --end 2025-12-31 --freeze 2025-07-01 --out finetune/evolve
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import trading_committee as tc  # noqa: E402
from agent.strategy_engine import run_backtest_realistic  # noqa: E402
from agent.strategy_schema import validate_strategy_config  # noqa: E402
from akshare_client import get_history  # noqa: E402
from finetune.replay_factory import (  # noqa: E402
    INITIAL_CASH, _apply_decision, _apply_hard_stop, _forward_return,
    _load_dotenv, _stop_pct_of, _trading_days,
)

WEEK_LEN = 5          # 多少个决策日算一个"周"
MIN_BARS_FOR_GATE = 60  # 回测闸门需要的最少历史（否则提案暂缓）

# 沙盘实验角色卡通道：2026-10-13 第二轮 A/B 验证通过的退出纪律卡
# （持仓时评估"继续持有的证据是否仍成立"；评估卖出与持有同样认真）
# **已毕业合入生产 _ROLE_SYSTEM**——这里保持空表。下一轮沙盘实验（如
# 卖出灵敏度分级：只在趋势结构破坏时卖、不在短线转弱时卖）在这里挂新卡，
# 生产提示词永远只接收 A/B 验证过的内容。
SANDBOX_ROLE_OVERRIDES = {}

REFLECT_SYSTEM = (
    "你是这条策略的周复盘官。输入是本周的决策记录、战绩与当前策略 config。"
    "提炼最多 3 条**可执行**的教训（犯了什么模式、什么该固化成规则），"
    '只输出 JSON：{"lessons": ["...", "..."]}'
)

PROPOSE_SYSTEM = (
    "你是策略进化师。输入是当前策略 config、截至本周的教训清单、以及委员会"
    "本周的实际决策。请给出**修改后的完整策略 config**（与输入同 schema）：\n"
    "- 可改的**不止入场**：出场条件（止盈/止损/均线离场）与仓位（size_pct）"
    "都在进化范围内——入场、出场、仓位是一个完整策略的三面，只优化入场是偏科；\n"
    "- 只改你认为有证据支撑的字段，其余原样保留；symbol 不许改（禁换标的是硬约束）；\n"
    "- 提案会被历史数据回测验证，不如现版就会被否决——别为了改而改；\n"
    '只输出一个 JSON 对象（config 本身），不要任何其他文字。'
)


def _llm_json(completion, system: str, user: str) -> dict | None:
    """一次调用 + 确定性 JSON 解析（读不懂返回 None，绝不猜）。

    completion 为 None 时回落 llm_service.chat_completion —— 与 run_committee
    同一条兜底纪律。pilot 实测踩过：没有这个回落，CLI 直跑时提案调用
    一次都没发出去，全部被误记成"提案不可解析"。
    """
    import re
    if completion is None:
        import llm_service
        completion = llm_service.chat_completion
    try:
        choice = completion(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            temperature=0.2, max_tokens=1200)
        text = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception:
        return None
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except Exception:
        return None


def _gate(records_so_far: list[dict], old_cfg: dict, new_cfg: dict,
          symbol: str) -> tuple[bool, str, dict, dict]:
    """回测闸门：新 config 必须在截至当时的历史上跑赢旧版才放行。"""
    if str(new_cfg.get("symbol") or "").strip() != symbol:
        return False, "提案试图换标的（硬约束）", {}, {}
    # 返回值是 (cfg, err) 元组：err 为 None 才算过（pilot 实测把元组当
    # 错误串用，导致所有合法提案都被"schema 失败"误杀）
    _, err = validate_strategy_config(new_cfg)
    if err:
        return False, f"schema 校验失败：{err}", {}, {}
    old_bt = run_backtest_realistic(old_cfg, records_so_far)
    new_bt = run_backtest_realistic(new_cfg, records_so_far)
    old_ret = float(old_bt.get("total_return_pct") or 0.0)
    new_ret = float(new_bt.get("total_return_pct") or 0.0)
    verdict = new_ret > old_ret
    reason = (f"回测对比（截至当时）：旧 {old_ret}% vs 新 {new_ret}% → "
              + ("应用" if verdict else "否决"))
    return verdict, reason, old_bt, new_bt


def evolve_replay(symbol: str, start: str, end: str, freeze: str,
                  config: dict, out_dir: str,
                  completion=None, budget_tokens: int = 0) -> dict:
    """跑完整场自进化回放。返回汇总报告（同时落盘三份产物）。"""
    os.makedirs(out_dir, exist_ok=True)
    chronicle_path = os.path.join(out_dir, "chronicle.md")
    history_path = os.path.join(out_dir, "config_history.json")
    report_path = os.path.join(out_dir, "report.json")

    fetch_end = (date.fromisoformat(end) + timedelta(days=30)).isoformat()
    records = get_history(symbol, "", fetch_end, "day") or []
    report = {"symbol": symbol, "start": start, "end": end, "freeze": freeze,
              "days": 0, "decisions": {"buy": 0, "sell": 0, "hold": 0,
                                       "hard_stop": 0, "mismatch": 0,
                                       "add_blocked": 0},
              "proposals": {"made": 0, "applied": 0, "rejected": 0,
                            "deferred": 0}, "tokens": 0, "error": None}
    if not records:
        report["error"] = f"没有取到 {symbol} 的历史数据"
        return report

    cfg = json.loads(json.dumps(config))   # 深拷贝，别污染调用方
    cfg.setdefault("symbol", symbol)
    history = [{"week": 0, "config": cfg, "event": "初始策略"}]

    position = {"cash": INITIAL_CASH, "shares": 0, "avg_cost": None}
    equity_curve: list[dict] = []
    week_days: list[dict] = []   # 本周逐日摘要（给反思/提案当原料）

    def call_llm(system: str, user: str):
        nonlocal budget_check
        result = _llm_json(completion, system, user)
        return result

    budget_check = 0

    def chronicle(line: str) -> None:
        with open(chronicle_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    chronicle(f"# {symbol} 自进化回放 {start} ~ {end}（冻结线 {freeze}）\n")

    day_in_week = 0
    week_no = 0
    consecutive_llm_failures = 0
    for as_of in _trading_days(records, start, end):
        if not any(str(r.get("date")) == as_of for r in records):
            continue
        report["days"] += 1
        day_in_week += 1
        price_row = next(r for r in records if str(r.get("date")) == as_of)
        close = float(price_row.get("close") or 0.0)
        day_low = float(price_row.get("low") or 0.0) or None

        # ---- 止损硬通道（镜像生产"生存线不等模型"）：先于委员会判 ----
        if _apply_hard_stop(position, day_low, _stop_pct_of(cfg)) == "hard_stop":
            report["decisions"]["hard_stop"] += 1
            chronicle(f"- **{as_of}** ⛔硬止损（low {day_low} 击穿 "
                      f"{_stop_pct_of(cfg)}% 线）｜ 清仓后现金 "
                      f"{round(position['cash'], 2)}")

        # ---- 轨 A：委员会看"截至今天"的证据做决策 ----
        evidence, warnings = tc.build_evidence(symbol, as_of, records, position)
        if not evidence:
            continue
        # 策略意图注入：当前 config 的规则作为"本周生效的策略指令"进证据
        evidence["strategy_intent"] = {
            "name": cfg.get("name"), "entry": cfg.get("entry"),
            "exit": cfg.get("exit"), "position": cfg.get("position"),
            "note": "本周生效的策略指令（你每周可能修改它）；可以偏离，但偏离要给理由。",
        }
        result = tc.run_committee(symbol, as_of, evidence, list(warnings),
                                  completion=completion,
                                  role_system=SANDBOX_ROLE_OVERRIDES)
        report["tokens"] += result.total_tokens
        # LLM 连续两天没调成（余额耗尽/服务故障）→ 显式中止，不产出假 HOLD 天：
        # 2026-10-12 实测踩过（402 Payment Required 被当成普通失败，
        # 静默跑出 18 天 HOLD x0.0 的废轨迹，还差点被当成行为结果）
        if result.llm_calls == 0:
            consecutive_llm_failures += 1
            if consecutive_llm_failures >= 2:
                report["error"] = ("LLM 连续调用失败，已中止（检查 API 余额/服务状态；"
                                   "本日之前的轨迹有效）")
                chronicle(f"- ⛔ {as_of}：LLM 连续两天未调成，中止回放")
                break
        else:
            consecutive_llm_failures = 0
        decision = result.decision
        fwd = _forward_return(records, as_of, 5)
        aligned = None
        if decision.decision in ("buy", "sell") and fwd is not None:
            aligned = (fwd if decision.decision == "buy" else -fwd) > 0
        # 加仓趋势过滤（第二轮 A/B 诊断）：加仓只允许"浮盈 + 现价贴近 20 日高点"
        # （3% 以内）——第一轮三次亏损加仓全是回调中接，角色卡约束不住的行为
        # 用代码兜底。开仓不受限（空仓进场是策略的本职）。
        idx = next(i for i, r in enumerate(records) if str(r.get("date")) == as_of)
        window20 = [float(r.get("close") or 0.0)
                    for r in records[max(0, idx - 19):idx + 1]]
        near_high = (max(window20) > 0
                     and close >= max(window20) * 0.97)
        in_profit = (position["shares"] == 0
                     or (position.get("avg_cost") and close > position["avg_cost"]))
        action = _apply_decision(position, decision.as_dict(), close,
                                 allow_add=near_high and in_profit)
        if action == "add_blocked":
            report["decisions"]["add_blocked"] += 1
            chronicle(f"- **{as_of}** 🚫加仓被趋势过滤拦截（回调/浮亏中不接）")
            action = "hold"
        if action == "skip_mismatch":
            # 空仓时发卖出：留痕（生产是 state_mismatch 跳过，旧沙盘是静默——
            # 静默让诊断只能靠数学反推，2026-10-12 的教训）
            report["decisions"]["mismatch"] += 1
            chronicle(f"- **{as_of}** ⚠信号矛盾（空仓时 SELL），留痕跳过")
            action = "hold"
        action_label = {"open": "开仓", "add": "加仓", "sell": "清仓",
                        "partial_sell": "部分卖"}.get(action, "HOLD")
        report["decisions"][{"open": "buy", "add": "buy", "sell": "sell",
                             "partial_sell": "sell"}.get(action, "hold")] += 1
        judge_line = next((str(e.get("excerpt") or "")[:120]
                           for e in result.transcript
                           if e.get("role") == "risk_judge"), "")
        chronicle(f"- **{as_of}** {action_label}（{decision.decision.upper()} "
                  f"x{decision.size_fraction}）"
                  + (f"（{'对' if aligned else '错'}，5日 {fwd * 100:+.1f}%）"
                     if aligned is not None else "")
                  + f" ｜ 持仓 {position['shares']} 股 ｜ {judge_line}")
        week_days.append({"date": as_of, "decision": decision.decision,
                          "action": action,
                          "signal": decision.signal,
                          "size": decision.size_fraction, "fwd_pct":
                          round(fwd * 100, 2) if fwd is not None else None,
                          "aligned": aligned})
        equity_curve.append({"date": as_of,
                             "equity": position["cash"] + position["shares"] * close})

        # ---- 轨 B：周末反思 + 提案 + 回测闸门（冻结线后停） ----
        if day_in_week >= WEEK_LEN:
            day_in_week = 0
            week_no += 1
            if as_of >= freeze:
                chronicle(f"\n## 第 {week_no} 周（{as_of}，冻结段——不再提案）\n")
                week_days = []
                continue
            records_so_far = [r for r in records if str(r.get("date")) <= as_of]
            # 闸门回测只看**最近一年**（250 根）：全历史对比会把"2025 年的教训"
            # 淹没在十年数据里——进化验证的是近期市场里的相对优劣
            records_so_far = records_so_far[-250:]
            wins = sum(1 for d in week_days if d["aligned"] is True)
            judged = sum(1 for d in week_days if d["aligned"] is not None)
            reflect_user = json.dumps({
                "symbol": symbol, "week": week_days,
                "stats": {"judged": judged, "wins": wins},
                "current_config": cfg}, ensure_ascii=False, default=str)
            lessons = (call_llm(REFLECT_SYSTEM, reflect_user) or {}).get("lessons") or []
            chronicle(f"\n## 第 {week_no} 周（{as_of}）反思："
                      + ("；".join(str(l)[:60] for l in lessons[:3]) or "无教训")
                      + "\n")
            if len(records_so_far) < MIN_BARS_FOR_GATE:
                report["proposals"]["deferred"] += 1
                chronicle(f"- 提案暂缓：历史不足 {MIN_BARS_FOR_GATE} 根，回测闸门开不了")
                week_days = []
                continue
            propose_user = json.dumps({
                "current_config": cfg, "lessons": lessons,
                "week_decisions": week_days}, ensure_ascii=False, default=str)
            new_cfg = call_llm(PROPOSE_SYSTEM, propose_user)
            report["proposals"]["made"] += 1
            if not isinstance(new_cfg, dict) or not new_cfg:
                report["proposals"]["rejected"] += 1
                chronicle("- 提案不可解析（读不懂就不动）")
                week_days = []
                continue
            ok, reason, old_bt, new_bt = _gate(records_so_far, cfg, new_cfg, symbol)
            chronicle(f"- 提案：{json.dumps({k: new_cfg.get(k) for k in ('entry', 'exit', 'position')}, ensure_ascii=False)[:300]}")
            chronicle(f"- 闸门：{reason}")
            if ok:
                cfg = new_cfg
                report["proposals"]["applied"] += 1
                history.append({"week": week_no, "config": cfg,
                                "event": reason, "old_bt": old_bt, "new_bt": new_bt})
            else:
                report["proposals"]["rejected"] += 1
            week_days = []
        if budget_tokens and report["tokens"] >= budget_tokens:
            report["stopped_at"] = "token_budget"
            break

    # ---- 汇总：轨 A 绩效 vs 买入持有 vs 最终 config 全区间回测 ----
    final_equity = equity_curve[-1]["equity"] if equity_curve else INITIAL_CASH
    peak, max_dd = 0.0, 0.0
    for point in equity_curve:
        peak = max(peak, point["equity"])
        max_dd = max(max_dd, (peak - point["equity"]) / peak if peak > 0 else 0.0)
    first_close = float((records[0] if records else {}).get("close") or 0.0)
    window = [r for r in records if start <= str(r.get("date")) <= end]
    bh_ret = ((float(window[-1]["close"]) / float(window[0]["close"]) - 1) * 100
              if window and float(window[0].get("close") or 0) > 0 else None)
    # 最终 config 的对照回测**只跑回放窗口**：pilot 实测踩过坑——get_history
    # 的 start="" 会取全历史（前复权），全段跑出来的收益率是天文数字，
    # 和"这一年的成绩"完全是两件事。窗口不足 21 根（回测引擎下限）时如实标注。
    if len(window) >= 21:
        final_bt = run_backtest_realistic(cfg, window)
        final_bt_view = {k: final_bt.get(k) for k in
                         ("total_return_pct", "buy_and_hold_return_pct",
                          "excess_return_pct")}
    else:
        final_bt_view = {"error": "回放窗口不足 21 根，回测引擎下限"}
    report.update({
        "final_equity": round(final_equity, 2),
        "track_a_return_pct": round((final_equity / INITIAL_CASH - 1) * 100, 2),
        "track_a_max_drawdown_pct": round(max_dd * 100, 2),
        "buy_hold_return_pct": round(bh_ret, 2) if bh_ret is not None else None,
        "final_config_backtest": final_bt_view,
        "config_versions": len(history),
    })
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    chronicle(f"\n# 汇总\n轨A（委员会实际决策）{report['track_a_return_pct']}%/"
              f"回撤 {report['track_a_max_drawdown_pct']}% ｜ 买入持有 "
              f"{report['buy_hold_return_pct']}% ｜ 最终config回测 "
              f"{report['final_config_backtest'].get('total_return_pct')}% ｜ "
              f"提案 {report['proposals']}")
    return report


DEFAULT_CONFIG = {
    "schema_version": "1.0",
    "name": "601988-趋势跟随（进化起点）",
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
    parser = argparse.ArgumentParser(description="自进化回放（沙盘一年）")
    parser.add_argument("--symbol", default="601988")
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--freeze", required=True, help="冻结线（此后不再提案）")
    parser.add_argument("--out", default="finetune/evolve")
    parser.add_argument("--config", help="起始 config JSON 文件（缺省用内置模板）")
    parser.add_argument("--budget-tokens", type=int, default=0)
    args = parser.parse_args()

    _load_dotenv()
    cfg = DEFAULT_CONFIG
    if args.config:
        with open(args.config, encoding="utf-8") as f:
            cfg = json.load(f)
    cfg["symbol"] = args.symbol
    report = evolve_replay(args.symbol, args.start, args.end, args.freeze,
                           cfg, args.out, budget_tokens=args.budget_tokens)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
