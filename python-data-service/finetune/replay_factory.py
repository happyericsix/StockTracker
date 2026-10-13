# -*- coding: utf-8 -*-
"""replay_factory.py —— 交易决策数据工厂（finetune 管线的第二个数据源）。

对历史行情逐交易日"重放委员会"：每个决策日用**与线上同一套** build_evidence /
run_committee 产决策，再用后续真实行情当场对账（T+N 前向收益 → 方向对齐）。
产出 ShareGPT 样本（与 build_dataset.py 同格式），SFT 全量可用；
meta.outcome 旁挂对齐标签，供将来 DPO/RL 构偏好对。

<h3>与两个既有层的关系（别再混淆）</h3>
- 记忆层（facts/lessons/经验回流）：线上运行时攒的**经验**，进提示词；
- build_dataset.py：第一个数据源——线上攒的**已解读资讯**（342 条，<500 微调线）；
- 本脚本：第二个数据源——**回放的交易决策**。一台机器一天能产几百个带完整
  证据与真实结果的样本，不受"一天只有一场委员会"的现实节律限制。

<h3>诚实边界（训练前必读）</h3>
1. 仓位的执行演化是**简化账本**（整手 + 滑点，见 _apply_decision）：不带 Java
   applyBarResult 的涨跌停/印花税/T+1 全套纪律。训练的是**决策函数**，执行纪律
   在线上由代码保证——两者本来就分离。
2. SFT 的监督信号是 judge 的决策文本（DeepSeek 风格蒸馏，同 build_dataset 任务 A
   的诚实边界）；outcome 只做旁挂标签，不进监督文本——防模型学会"从答案倒推"。
3. 成本真实发生：一场委员会 8 次 LLM 调用（~2 万 token）。--max-samples 是硬顶，
   结束时会打印 token 用量；估算：50 样本 ≈ 100 万 token。

用法（python-data-service/ 目录）：
    python finetune/replay_factory.py --symbols 601988 --start 2025-01-01 \
        --end 2025-03-31 --max-samples 20 --out finetune/dataset
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import trading_committee as tc  # noqa: E402
from agent.trading_decision import decision_prompt_contract  # noqa: E402
from akshare_client import get_history  # noqa: E402

LOT = 100
SLIPPAGE = 0.001
INITIAL_CASH = 100_000.0
JUDGE_SYSTEM = tc._ROLE_SYSTEM["risk_judge"] + "\n\n" + decision_prompt_contract()


def _load_dotenv(path: str = ".env") -> None:
    """CLI 场景下 llm_service 的 key 只在环境里；.env 存在就带上（服务里由 app 加载）。"""
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip())


def _trading_days(records: list[dict], start: str, end: str) -> list[str]:
    return [str(r.get("date")) for r in records
            if start <= str(r.get("date")) <= end]


def _forward_return(records: list[dict], as_of: str, horizon: int) -> float | None:
    """as_of → 第 horizon 个交易日的收盘收益。bar 不够（回放段尾部）返回 None。"""
    dates = [str(r.get("date")) for r in records]
    if as_of not in dates:
        return None
    idx = dates.index(as_of)
    future = idx + horizon
    if future >= len(records):
        return None
    entry = float(records[idx].get("close") or 0.0)
    exit_ = float(records[future].get("close") or 0.0)
    return exit_ / entry - 1.0 if entry > 0 else None


def _apply_decision(position: dict, decision: dict, price: float | None,
                    allow_add: bool = True) -> str:
    """账本 v3（2026-10-13 第二轮 A/B 后）：

    - **decision 压过 signal**（修口径违规）：decision=skip 时无论 signal 说什么
      都不下单——与生产 applyBarResult 同一条铁律。第一轮实测踩过：规范行
      SELL + 裁决 SKIP，沙盘按 signal 清了仓，卖在阶段低点；
    - **加仓趋势过滤**（allow_add=False 时拦截）：加仓只在"对己有利的波动后"
      ——现价高于持仓成本且贴近 20 日高点（回调中接飞刀的加仓一律拦下）。
      角色卡写了同样的纪律但模型不遵守：**提示词约束弱于代码约束**，
      这一层是代码兜底（与生产"三层夹取"同一哲学）；
    - v2 的加仓/部分卖出/矛盾留痕语义不变。
    """
    signal = str(decision.get("signal") or "").lower()
    verdict = str(decision.get("decision") or "").lower()
    size = float(decision.get("size_fraction") or 0.0)
    if price is None or price <= 0:
        return "none"
    if verdict == "skip":
        return "none"          # 铁律：读不懂/裁决不动 → 什么单都不下
    fraction = size if 0 < size <= 1 else 1.0

    if signal == "buy":
        fill = price * (1 + SLIPPAGE)
        budget = position["cash"] * fraction
        shares = int(budget / (fill * LOT)) * LOT
        if shares <= 0:
            return "none"
        if position["shares"] > 0:
            if not allow_add:
                return "add_blocked"
            # 加仓：加权成本（现金不足一手时自然 no-op）
            total_cost = (position["shares"] * (position["avg_cost"] or fill)
                          + shares * fill)
            position["shares"] += shares
            position["avg_cost"] = total_cost / position["shares"]
            position["cash"] -= shares * fill
            return "add"
        position["shares"] = shares
        position["avg_cost"] = fill
        position["cash"] -= shares * fill
        return "open"

    if signal == "sell":
        if position["shares"] <= 0:
            return "skip_mismatch"
        fill = price * (1 - SLIPPAGE)
        held = position["shares"]
        sell_shares = held
        partial = False
        if fraction < 1.0:
            sell_shares = int(held * fraction / LOT) * LOT
            if 0 < sell_shares < held:
                partial = True
            else:
                sell_shares = held   # 取整后不足一手 → 按全仓（A股卖不了零股）
        position["cash"] += sell_shares * fill
        position["shares"] = held - sell_shares
        if position["shares"] == 0:
            position["avg_cost"] = None
        return "partial_sell" if partial else "sell"
    return "none"


def _apply_hard_stop(position: dict, day_low: float | None,
                     stop_pct: float = -8.0) -> str:
    """止损硬通道（沙盘版，镜像生产"生存线不等模型"的纪律）：

    当日最低价击穿 `avg_cost × (1 + stop_pct/100)` 即按止损价成交全卖。
    用日线 low 近似盘中触发——不需要分钟数据（OHLC 已含盘中信息）。
    返回 "hard_stop" 或 "none"。
    """
    if position["shares"] <= 0 or not position.get("avg_cost") or day_low is None:
        return "none"
    stop_price = position["avg_cost"] * (1 + stop_pct / 100.0)
    if day_low > stop_price:
        return "none"
    fill = min(stop_price, day_low) * (1 - SLIPPAGE)   # 按止损价与实际低点的较差者成交
    position["cash"] += position["shares"] * fill
    position["shares"] = 0
    position["avg_cost"] = None
    return "hard_stop"


def _stop_pct_of(config: dict) -> float:
    """从策略 config 的出场条件里取止损百分比（缺省 -8）。"""
    for cond in ((config.get("exit") or {}).get("conditions") or []):
        if cond.get("type") == "stop_loss_pct" and cond.get("value"):
            try:
                return float(cond["value"])
            except (TypeError, ValueError):
                continue
    return -8.0


def replay_symbol(symbol: str, start: str, end: str, horizon: int = 5,
                  max_samples: int = 10, out_path: str = "",
                  completion=None, budget_tokens: int = 0) -> dict:
    """回放一只票的区间，追加样本到 out_path。返回统计（供 CLI 与测试共用）。"""
    # 对账需要回放段**之外**的 horizon 根 bar：取数终点多带 30 个自然日
    # （5 个交易日 × 节假日余量）。冒烟实测踩过：不带缓冲，最后几天的
    # forward_return 全是 None，样本带着空标签进了数据集。
    from datetime import date, timedelta
    fetch_end = (date.fromisoformat(end) + timedelta(days=30)).isoformat()
    records = get_history(symbol, "", fetch_end, "day") or []
    stats = {"symbol": symbol, "days": 0, "samples": 0, "skipped_no_bar": 0,
             "tokens": 0, "written": 0}
    if not records:
        stats["error"] = f"没有取到 {symbol} 的历史数据"
        return stats

    done: dict[str, dict] = {}
    if out_path and os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                meta = json.loads(line).get("meta", {})
                if meta.get("sample_id"):
                    done[meta["sample_id"]] = meta.get("decision") or {}

    position = {"cash": INITIAL_CASH, "shares": 0, "avg_cost": None}
    for as_of in _trading_days(records, start, end):
        # 复刻 decide 的硬守卫：决策日必须有当日 bar（防用前一日收盘冒充）
        if not any(str(r.get("date")) == as_of for r in records):
            stats["skipped_no_bar"] += 1
            continue
        stats["days"] += 1
        evidence, warnings = tc.build_evidence(symbol, as_of, records, position)
        if not evidence:
            stats["skipped_no_bar"] += 1
            continue
        sample_id = f"replay-{symbol}-{as_of}"
        last_close = evidence.get("last_close")
        if sample_id in done:
            # 断点续跑：已产出的决策日不再开会（省一次委员会的钱），
            # 但仓位演化必须重放它的决策 —— 否则后续证据包里的持仓是错的
            _apply_decision(position, done[sample_id], last_close)
            continue
        fwd = _forward_return(records, as_of, horizon)
        result = tc.run_committee(symbol, as_of, evidence, list(warnings),
                                  completion=completion)
        stats["tokens"] += result.total_tokens
        decision = result.decision
        outcome = {"horizon_days": horizon, "forward_return_pct":
                   round(fwd * 100, 2) if fwd is not None else None}
        if decision.decision in ("buy", "sell") and fwd is not None:
            aligned = fwd if decision.decision == "buy" else -fwd
            outcome["aligned"] = aligned > 0
            outcome["aligned_pct"] = round(aligned * 100, 2)
        if result.llm_calls == 0 or not result.transcript:
            continue   # 没开成会（LLM 不可用）：不产样本也不算天
        if out_path:
            sample = {
                "conversations": [
                    {"from": "system", "value": JUDGE_SYSTEM},
                    {"from": "human", "value": json.dumps(
                        {"evidence": evidence,
                         "note": "基于以上决策日可得证据给出交易决策。"},
                        ensure_ascii=False, default=str)},
                    {"from": "gpt", "value": _judge_text_of(result)},
                ],
                "task": "trade_decision",
                "meta": {"sample_id": sample_id, "source": "replay",
                         "as_of": as_of, "decision": decision.as_dict(),
                         "outcome": outcome,
                         "model": result.model,
                         "prompt_version": tc.prompt_version()},
            }
            with open(out_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(sample, ensure_ascii=False) + "\n")
            stats["written"] += 1
        stats["samples"] += 1
        _apply_decision(position, decision.as_dict(), last_close)
        if budget_tokens and stats["tokens"] >= budget_tokens:
            stats["stopped_at"] = "token_budget"
            break
        if stats["samples"] >= max_samples:
            break
    return stats


def _judge_text_of(result) -> str:
    """风控裁决的原话（训练监督信号）。没有裁决文本的样本不值得训。"""
    for entry in result.transcript:
        if entry.get("role") == "risk_judge":
            return str(entry.get("excerpt") or "")
    return ""


def main() -> None:
    parser = argparse.ArgumentParser(description="交易决策数据工厂（历史回放）")
    parser.add_argument("--symbols", required=True, help="逗号分隔，如 601988,600519")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD")
    parser.add_argument("--horizon", type=int, default=5, help="对账窗口（交易日）")
    parser.add_argument("--max-samples", type=int, default=10, help="每标的样本硬顶")
    parser.add_argument("--budget-tokens", type=int, default=0, help="0=不限")
    parser.add_argument("--out", default="finetune/dataset/replay_decision.jsonl")
    args = parser.parse_args()

    _load_dotenv()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    total = {"samples": 0, "tokens": 0}
    for symbol in [s.strip() for s in args.symbols.split(",") if s.strip()]:
        stats = replay_symbol(symbol, args.start, args.end, args.horizon,
                              args.max_samples, args.out,
                              budget_tokens=args.budget_tokens)
        total["samples"] += stats["samples"]
        total["tokens"] += stats["tokens"]
        print(json.dumps(stats, ensure_ascii=False))
    print(f"完成：{total['samples']} 个样本，共 {total['tokens']} tokens "
          f"（写往 {args.out}）")


if __name__ == "__main__":
    main()
