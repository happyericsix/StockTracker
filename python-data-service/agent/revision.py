# -*- coding: utf-8 -*-
"""策略修订闭环（P2 落地，2026-10-15）。

沙盒 evolve_replay 已验证"提案 → 回测闸门"的形状；本模块把它接到生产：
委员会/规则跑了一段时间 → 确定性健康信号说"策略可能有问题" →
一次 LLM 调用产出**结构化参数提案** → 立刻用真实化回测验证 →
带证据交人审批。永不在本模块内改配置（落库与 configUpdatedAt 由 Java 审批路径负责）。

## 时间衰减加权（本次的产品要求）
回测证据不允许"永远停在 2025"：数据窗口一律**截到最新一根 bar**，
并按指数衰减加权 —— 离今天越近权重越高，半衰期 HALF_LIFE_TRADING_DAYS。
门禁主判据是加权收益（近期主导），但保留两条全期护栏（防"只为最近两周过拟合"）。

确定性纪律与全库一致：数字全部由代码算，LLM 只产出提案文本与参数 diff 的动机，
读不懂 / 校验不过 / 门禁不过 → 明说，绝不半推半就放行。
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, timedelta

# ---------------- 常量（门禁口径，动它们 = 换了一把尺子，需在 UI 同步说明） ----------------

#: 加权半衰期（交易日）：60 天前的 bar 权重减半，120 天前剩 1/4
HALF_LIFE_TRADING_DAYS = 60
#: 数据窗口长度（bar 数）：始终取**截至今天**的最近 N 根，起点随时间前移
DATA_WINDOW_BARS = 500
#: 门禁开闸所需最少 bar 数（MA60 预热 + 有意义的最短评估段）
MIN_BARS_FOR_GATE = 120
#: 全期收益护栏：新配置全期收益允许比旧版低多少个百分点（防只讨好近期）
FULL_PERIOD_TOLERANCE_PT = 5.0
#: 回撤护栏：新配置近期加权回撤允许比旧版大多少个百分点
DRAWDOWN_TOLERANCE_PT = 3.0

# ---- 两段式门禁与多重检验防御（2026-10-15 过拟合实验后新增）----
# 实验结论（SZ300750，截至 2026-10-15）：同一窗口 25 组随机参数全期收益 -7%~+84%；
# 样本内排名与最近 60 根留出段收益的 Spearman 秩相关 -0.55（样本内最优是反向指标）；
# 基准加权年化 -17% 时随机参数 84% 能"跑赢"它 —— 弱基准下门禁没有甄别力。
#: 留出段长度（bar）：只验证、不参与挑选，专杀"样本内好看、最近就翻车"的提案
HOLDOUT_BARS = 40
#: 留出段容忍（pt）：新配置留出段只许比旧版差 1pt 以内（要求赢会制造新的多重检验面）
HOLDOUT_TOLERANCE_PT = 1.0
#: 弱基准线（加权年化 %）：低于它的基准，随机参数大概率"跑赢"，门槛必须提高
WEAK_BASELINE_PCT = -15.0
#: 弱基准时的最小改善幅度（pt）：决策段加权年化至少多赚这么多才算真改善
WEAK_BASELINE_MARGIN_PT = 5.0

# ---- 复杂度罚金（BIC 式，2026-10-16 补齐门禁的最后一块）----
# 模型选择的经典结论：每多一个自由度都要用样本外改善来付账——BIC 对每个参数收
# k·ln(n)；Bailey/Harvey/Liu 的 Covariance-Penalty（arXiv:1905.05023）把同一精神带回
# 回测：样本内夏普的期望折减 ≈ 2·有效自由度/N。映射到本门禁：策略复杂度以
# entry+exit 的条件条数计（strategy_complexity），提案每净增一条条件，决策段
# 加权年化的改善门槛抬高 COMPLEXITY_PENALTY_PT。数值与 WEAK_BASELINE_MARGIN_PT
# 同尺度——是校准的先验而非推导值：罚金管方向（不鼓励叠条件），真正的裁判
# 仍然是留出段反证。反向的"极简奖励"封顶一条条件的量（1-SE 规则的精神：
# 更简的模型允许稍微差一点，但不许拿删条件换大幅劣化）。
#: 每净增一条 DSL 条件收取的复杂度罚金（决策段加权年化 pt）
COMPLEXITY_PENALTY_PT = 5.0
#: 删条件的极简奖励上限（pt）：再简也不许用劣化超过这个数来换
COMPLEXITY_BONUS_MAX_PT = 5.0

REVISION_MAX_CHARS = 600

_PROPOSE_SYSTEM = (
    "你是这条模拟盘策略的修订提案师。输入：当前策略 config（DSL，字段不许增删种类）、"
    "它运行以来的客观健康材料（决策日志命中率、可验证预期判定、净值与回撤、近期成交、"
    "已沉淀的教训）、以及 current_performance —— 当前配置在**截至今天**行情上的回测表现"
    "（含近期加权年化收益与近期回撤：这是评估参数是否贴合当下行情的主要证据）。\n"
    "请给出修订提案，硬约束：\n"
    "- 只允许**参数级**修改：条件里的 window/value/direction/fast/slow、"
    "entry/exit 的 logic、条件条目可增删、position.size_pct、risk.slippage_pct；\n"
    f"- 新增条件是有代价的：门禁对每条净增条件收 {COMPLEXITY_PENALTY_PT:.0f}pt "
    "加权年化罚金（BIC 式复杂度问责——每条新规则都要用样本外改善付账），"
    f"删条件最多奖励 {COMPLEXITY_BONUS_MAX_PT:.0f}pt；能用调参数解决的事就不要加条件；\n"
    "- symbol 不许改（换票=新策略，硬约束）；schema_version/initial_capital/data 原样保留；\n"
    "- 每处修改必须能指认材料里的具体证据（健康预警/哪笔回撤/current_performance 的近期数字）；"
    "没有证据支撑的字段一律原样保留 —— 不为了改而改；"
    "若健康材料无预警，用户是主动要求评估：以 current_performance 的近期表现为主证据，"
    "近期加权年化为负或近期回撤深时，参数（均线窗口/止损止盈/仓位）通常有可议之处；\n"
    "若评估后确实认为无需任何修改，返回 config 与输入完全一致，并在 rationale 说明。\n"
    "- 提案会被「截至今天、近期加权」的真实化回测验证，跑不赢现版会被标为未过门禁。\n"
    "只输出一个 JSON 对象：{\"config\": 修改后的完整 config, "
    "\"rationale\": \"为什么这样改（引用材料数字，120 字内）\", "
    "\"expected_effect\": \"预期改善什么、可能付出什么代价（80 字内）\"}"
)


# ---------------- 确定性：时间衰减加权指标 ----------------

def recency_weighted_metrics(equity_curve: list[dict],
                             half_life_days: int = HALF_LIFE_TRADING_DAYS) -> dict:
    """对净值曲线做指数衰减加权评估。

    - weighted_daily_return_pct：Σ w_t·r_t / Σ w_t × 252（年化口径），
      w_t = 0.5 ** (age_t / half_life)，age = 距最后一根 bar 的交易日数；
    - weighted_max_drawdown_pct：**高权重区**（最近 2×半衰期）内的最大回撤 ——
      权重小到 1/4 以前的老回撤不该左右"现在还行不行"的判断；
    - recent_window_pct / weight_share_recent：把"近期占多少权重"说给人听。
    """
    n = len(equity_curve)
    if n < 2:
        return {"bars": n, "weighted_daily_return_pct": 0.0,
                "weighted_max_drawdown_pct": 0.0, "half_life_days": half_life_days}

    equities = [float(p.get("equity") or 0.0) for p in equity_curve]
    rets: list[float] = []
    weights: list[float] = []
    for i in range(1, n):
        prev = equities[i - 1]
        if prev <= 0:
            continue
        rets.append(equities[i] / prev - 1.0)
        age = (n - 1) - i            # 最后一根 bar age=0
        weights.append(0.5 ** (age / float(half_life_days)))

    wsum = sum(weights) or 1.0
    weighted_mean = sum(r * w for r, w in zip(rets, weights)) / wsum

    recent_len = min(n, 2 * half_life_days)
    recent = equities[n - recent_len:]
    peak = recent[0]
    worst = 0.0
    for v in recent:
        peak = max(peak, v)
        if peak > 0:
            worst = min(worst, v / peak - 1.0)

    recent_weight = sum(weights[-recent_len:]) if weights else 0.0
    return {
        "bars": n,
        "half_life_days": half_life_days,
        "weighted_daily_return_pct": round(weighted_mean * 252 * 100, 2),
        "weighted_max_drawdown_pct": round(worst * 100, 2),
        "recent_window_bars": recent_len,
        "recent_window_weight_share_pct": round(recent_weight / wsum * 100, 1),
    }


def _bt_view(backtest: dict, equity_key: str = "equity_curve") -> dict:
    """回测结果 → 展示/门禁用的扁平视图（全期 + 加权两套并排）。"""
    out = {
        "total_return_pct": float(backtest.get("total_return_pct") or 0.0),
        "excess_return_pct": float(backtest.get("excess_return_pct") or 0.0),
        "max_drawdown_pct": float(backtest.get("max_drawdown_pct") or 0.0),
        "closed_trades": int(backtest.get("closed_trades") or 0),
        "exposure_pct": float(backtest.get("exposure_pct") or 0.0),
    }
    curve = backtest.get(equity_key) or []
    out.update(recency_weighted_metrics(curve))
    return out


def _diff_params(old: dict, new: dict) -> list[dict]:
    """新旧 config 的确定性参数 diff（人能读的路由：entry/exit/position/risk）。"""
    diffs: list[dict] = []

    def walk(a, b, path):
        if isinstance(a, dict) and isinstance(b, dict):
            for key in sorted(set(a) | set(b)):
                if key not in a:
                    diffs.append({"path": f"{path}.{key}", "old": None, "new": b[key]})
                elif key not in b:
                    diffs.append({"path": f"{path}.{key}", "old": a[key], "new": None})
                else:
                    walk(a[key], b[key], f"{path}.{key}")
        elif a != b:
            diffs.append({"path": path, "old": a, "new": b})

    walk(old, new, "")
    # symbol/schema 等结构字段不是"参数调整"，单独列出以免混进参数区误导
    return diffs


# ---------------- 门禁：两段式（决策段加权主判 + 留出段反证） ----------------

def complexity_penalty_pt(old_cfg: dict, new_cfg: dict) -> dict:
    """新旧 config 的复杂度对账：净增条数 → 门槛调整量（pt）。

    adjustment_pt > 0 是罚金（加条件要付账），< 0 是封顶的极简奖励，0 是纯调参
    （条件条数不变，边际自由度没变——那种搜索噪声由留出段反证负责）。
    生产门禁与沙盘闸门（evolve_replay._gate）共用这一个函数：同一把尺，别处不许另算。
    """
    from agent.strategy_schema import strategy_complexity
    old_k = strategy_complexity(old_cfg)
    new_k = strategy_complexity(new_cfg)
    delta = new_k - old_k
    adjustment = max(delta * COMPLEXITY_PENALTY_PT, -COMPLEXITY_BONUS_MAX_PT)
    if delta > 0:
        note = (f"复杂度 {old_k} → {new_k} 条（净增 {delta}，"
                f"罚金 {adjustment:.0f}pt 已计入门槛）")
    elif delta < 0:
        note = (f"复杂度 {old_k} → {new_k} 条（净减 {-delta}，"
                f"极简奖励 {adjustment:.0f}pt，封顶 {COMPLEXITY_BONUS_MAX_PT:.0f}pt）")
    else:
        note = ""
    return {"old": old_k, "new": new_k, "delta": delta,
            "adjustment_pt": round(adjustment, 2), "note": note}


def _gate_view(backtest: dict, boundary_date: str) -> dict:
    """门禁专用视图：全期 + 全期加权 + 决策段加权 + 留出段收益/交易数。

    决策段 = 净值曲线上 <= boundary 的部分（提案被挑选时只看得到这段的数字）；
    留出段 = > boundary 的最近 HOLDOUT_BARS 根 —— 不参与任何挑选，只做反证。
    回测只跑一次（全窗口），两段都从同一条净值曲线上切，口径天然一致，
    且各自带足预热（长均线参数在留出段里不是"没预热"，是真的没信号）。
    """
    view = _bt_view(backtest)
    curve = backtest.get("equity_curve") or []
    trades = backtest.get("trade_log") or []
    decision_curve = [p for p in curve if str(p.get("date", ""))[:10] <= boundary_date]
    hold_curve = [p for p in curve if str(p.get("date", ""))[:10] > boundary_date]
    hold_trades = [t for t in trades if str(t.get("date", ""))[:10] > boundary_date]

    decision_weighted = recency_weighted_metrics(decision_curve)
    view["decision_weighted_daily_return_pct"] = decision_weighted.get(
        "weighted_daily_return_pct", 0.0)
    holdout_ret = None
    if decision_curve and hold_curve:
        base_eq = float(decision_curve[-1].get("equity") or 0.0)
        last_eq = float(hold_curve[-1].get("equity") or 0.0)
        if base_eq > 0:
            holdout_ret = round((last_eq / base_eq - 1.0) * 100, 2)
    view["holdout_return_pct"] = holdout_ret
    view["holdout_trades"] = len(hold_trades)
    return view


def revision_gate(records: list[dict], old_cfg: dict, new_cfg: dict) -> dict:
    """两段式回测门禁。返回 {passed, reason, old, new, window}；绝不抛异常。

    实验依据（见文件头）：样本内排名对未来几乎无信息，必须留一块从未参与
    挑选的数据做反证；弱基准下"跑赢"大概率是噪声，改善幅度门槛自动提高。
    """
    from agent.strategy_engine import run_backtest_realistic
    from agent.strategy_schema import validate_strategy_config

    boundary = str(records[-HOLDOUT_BARS].get("date", ""))[:10] if len(records) > HOLDOUT_BARS else ""
    window = {
        "bars": len(records),
        "start": str(records[0].get("date", ""))[:10] if records else "",
        "end": str(records[-1].get("date", ""))[:10] if records else "",
        "half_life_days": HALF_LIFE_TRADING_DAYS,
        "holdout_bars": HOLDOUT_BARS,
        "holdout_start": boundary,
        "note": (f"数据截至 {str(records[-1].get('date', ''))[:10] if records else '?'}，"
                 f"两段式验证：决策段（截至 {boundary}，按半衰期 {HALF_LIFE_TRADING_DAYS} 天"
                 f"指数衰减加权，越近权重越高）+ 留出段（最近 {HOLDOUT_BARS} 根，"
                 f"不参与挑选、只做反证）"),
    }

    # 复杂度对账提前算好：所有返回路径都带上，消费方（Java 证据链/测试）不用判空
    cx = complexity_penalty_pt(old_cfg, new_cfg)

    if str(new_cfg.get("symbol") or "").strip() != str(old_cfg.get("symbol") or "").strip():
        return {"passed": False, "reason": "提案试图换标的（硬约束：换票请新建策略）",
                "summary": "提案试图换成另一只股票，系统已拦截：换标的请直接新建一条策略。",
                "complexity": cx, "old": None, "new": None, "window": window}
    _, err = validate_strategy_config(new_cfg)
    if err:
        return {"passed": False, "reason": f"提案未通过 schema 校验：{err}",
                "summary": "提案的参数格式没通过系统检查，无法进入模拟验证。",
                "complexity": cx, "old": None, "new": None, "window": window}

    old_bt = run_backtest_realistic(old_cfg, records)
    new_bt = run_backtest_realistic(new_cfg, records)
    if old_bt.get("error") or new_bt.get("error"):
        return {"passed": False, "reason": "回测数据不足，门禁开不了（提案保留但不建议批准）",
                "summary": "历史行情数据不够，系统没法完成模拟对比，这份提案不建议批准。",
                "complexity": cx,
                "old": _bt_view(old_bt), "new": _bt_view(new_bt), "window": window}

    old_view, new_view = _gate_view(old_bt, boundary), _gate_view(new_bt, boundary)
    old_dw = old_view["decision_weighted_daily_return_pct"]
    new_dw = new_view["decision_weighted_daily_return_pct"]
    old_w = old_view["weighted_daily_return_pct"]
    old_full = old_view["total_return_pct"]
    new_full = new_view["total_return_pct"]
    old_dd = old_view["weighted_max_drawdown_pct"]
    new_dd = new_view["weighted_max_drawdown_pct"]
    old_ho = old_view["holdout_return_pct"]
    new_ho = new_view["holdout_return_pct"]

    # 弱基准加码：基准加权年化深负时，随机参数大概率"跑赢"，改善必须有余量
    margin = (WEAK_BASELINE_MARGIN_PT
              if old_w < WEAK_BASELINE_PCT else 0.0)
    margin_note = (f"（基准 {old_w}% 属弱基准，要求至少改善 {WEAK_BASELINE_MARGIN_PT:.0f}pt）"
                   if margin else "")
    # 复杂度罚金：净增条件抬高门槛，净减条件给封顶奖励（见 complexity_penalty_pt）
    cx_note = f"，{cx['note']}" if cx["note"] else ""
    threshold = old_dw + margin + cx["adjustment_pt"]

    # 主判据：决策段加权收益必须更好（留出段之外的全部数据，近期主导）
    if new_dw <= threshold:
        reason = (f"决策段加权年化未达标（旧 {old_dw}% vs 新 {new_dw}%，"
                  f"需 > {threshold:.2f}%{margin_note}{cx_note}）——"
                  "按「贴近当前行情」的口径不予放行")
        summary = (f"用最近两年的真实行情做模拟对比：换成新参数后，"
                   f"在近期行情下的年化收益反而更低（{old_dw:.2f}% → {new_dw:.2f}%），"
                   "没有跑赢现在的配置，系统不建议采用这次修改。")
        passed = False
    elif new_full < old_full - FULL_PERIOD_TOLERANCE_PT:
        reason = (f"全期收益回撤过深（旧 {old_full}% vs 新 {new_full}%，"
                  f"容忍 {FULL_PERIOD_TOLERANCE_PT}pt）——只讨好近期、牺牲全程的提案不放行")
        summary = (f"新参数虽然近期表现更好，但拉长到整个模拟期，总收益下降太多"
                   f"（{old_full:.2f}% → {new_full:.2f}%）——"
                   "像是只迎合了最近一小段行情，系统不建议采用。")
        passed = False
    elif new_dd < old_dd - DRAWDOWN_TOLERANCE_PT:
        # 回撤是负数：更差 = 更负（新比旧再深 3pt 以上才算恶化，实测踩过反号误判）
        reason = (f"近期最大回撤显著恶化（旧 {old_dd}% vs 新 {new_dd}%，"
                  f"容忍 {DRAWDOWN_TOLERANCE_PT}pt）")
        summary = (f"新参数近期「从最高点回落的最大幅度」明显更深（{old_dd:.2f}% → {new_dd:.2f}%），"
                   "亏损风险反而变大，系统不建议采用。")
        passed = False
    elif old_ho is not None and new_ho is not None and new_ho < old_ho - HOLDOUT_TOLERANCE_PT:
        reason = (f"留出段反证不通过（最近 {HOLDOUT_BARS} 根：旧 {old_ho}% vs 新 {new_ho}%，"
                  f"容忍 {HOLDOUT_TOLERANCE_PT}pt）—— 挑选时看不到的最近行情里，"
                  "提案配置立刻跑输，样本内优势大概率是过拟合")
        summary = (f"新参数在最近 {HOLDOUT_BARS} 个交易日里立刻落后于现版"
                   f"（{old_ho:.2f}% → {new_ho:.2f}%）——"
                   "这段行情没有参与过评估，说明之前的好表现更像是碰巧贴合历史，"
                   "系统不建议采用。")
        passed = False
    else:
        hold_note = (f"，留出段 {old_ho}% → {new_ho}%"
                     if old_ho is not None and new_ho is not None
                     else "，留出段数据不足（跳过反证）")
        reason = (f"门禁通过：决策段加权年化 {old_dw}% → {new_dw}%{margin_note}{cx_note}，"
                  f"全期 {old_full}% → {new_full}%，近期回撤 {old_dd}% → {new_dd}%{hold_note}")
        summary = (f"模拟对比通过：近期行情下的年化收益 {old_dw:.2f}% → {new_dw:.2f}%，"
                   f"整个模拟期总收益 {old_full:.2f}% → {new_full:.2f}%，"
                   f"近期最深回落 {old_dd:.2f}% → {new_dd:.2f}%。"
                   "新参数整体更好，可以批准应用。")
        passed = True

    return {"passed": passed, "reason": reason, "summary": summary, "complexity": cx,
            "old": old_view, "new": new_view, "window": window}


def _recent_share_hint() -> str:
    """高权重区权重占比的理论值（几何级数求和），只用于 UI 说明。"""
    hl = float(HALF_LIFE_TRADING_DAYS)
    r = 0.5 ** (1.0 / hl)
    total = 1.0 / (1.0 - r)
    recent = r ** 0 + sum(r ** (age + 1) for age in range(2 * int(hl)))
    return f"{recent / total * 100:.0f}%"


# ---------------- 提案半环：1 次 LLM ----------------

def propose_revision(payload: dict, completion=None) -> dict:
    """健康材料 → 结构化提案（1 次 LLM 调用）+ 确定性门禁。永不抛异常。"""
    import llm_service

    if not isinstance(payload, dict) or not isinstance(payload.get("config"), dict):
        return {"ok": False, "error": "payload 缺少 config 字段"}
    cfg_old = payload["config"]
    symbol = str(cfg_old.get("symbol") or "").strip()

    # 数据窗口：永远截到今天（fetch 多取 30 天缓冲保证拿到最新 bar）
    try:
        from akshare_client import get_history
        fetch_end = (date.today() + timedelta(days=30)).isoformat()
        records = get_history(symbol, "", fetch_end, "day", count=DATA_WINDOW_BARS) or []
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"行情数据获取失败（{type(exc).__name__}）"}
    records = records[-DATA_WINDOW_BARS:]
    if len(records) < MIN_BARS_FOR_GATE:
        return {"ok": False,
                "error": f"可用数据 {len(records)} 根，不足 {MIN_BARS_FOR_GATE} 根，开不了门禁"}

    # BYOK（2026-10-25）：completion 绑定了用户供应商时按用户的 Key 判定可用性
    checker = getattr(llm_service, "completion_available", None) \
        or getattr(llm_service, "_is_available", None)
    if callable(checker):
        try:
            if not checker(completion):
                return {"ok": False, "error": "模型服务不可用，改天再提"}
        except Exception:  # noqa: BLE001
            pass

    # 先把"当前配置截至今天的回测表现"算出来喂给提案师：没有健康预警时，
    # 近期加权表现就是唯一可依据的证据（也是"贴合当前行情"的落点）
    from agent.strategy_engine import run_backtest_realistic
    try:
        old_bt = run_backtest_realistic(cfg_old, records)
        if not old_bt.get("error"):
            payload = dict(payload)
            payload["current_performance"] = _bt_view(old_bt)
    except Exception:  # noqa: BLE001 —— 喂不上就少一份证据，不拦提案
        pass

    call = completion or llm_service.chat_completion
    try:
        choice = call(
            [{"role": "system", "content": _PROPOSE_SYSTEM},
             {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}],
            temperature=0.2, max_tokens=1400)
        text = str((((choice or {}).get("message") or {}).get("content")) or "")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"提案调用失败（{type(exc).__name__}）"}
    if "AI 服务暂不可用" in text:
        return {"ok": False, "error": "模型服务占位响应"}

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return {"ok": False, "error": "模型返回无法解析为提案"}
    try:
        data = json.loads(match.group(0))
    except Exception:  # noqa: BLE001
        return {"ok": False, "error": "提案 JSON 解析失败"}
    cfg_new = data.get("config")
    if not isinstance(cfg_new, dict) or not cfg_new:
        return {"ok": False, "error": "提案缺少 config 字段"}
    rationale = str(data.get("rationale") or "").strip()[:REVISION_MAX_CHARS]
    expected = str(data.get("expected_effect") or "").strip()[:300]

    diffs = _diff_params(cfg_old, cfg_new)
    if not diffs:
        # 模型评估后认为无需改：这不是失败，是一条有价值的结论（说给人听）
        return {"ok": False, "no_change": True,
                "error": "提案师评估后认为当前参数无需修改（未找到有证据支撑的调整点），"
                         "这与「不为改而改」的纪律一致"}

    gate = revision_gate(records, cfg_old, cfg_new)
    return {
        "ok": True,
        "proposal": {
            "config": cfg_new,
            "diff": diffs,
            "rationale": rationale or "（模型未给出理由）",
            "expected_effect": expected or "（模型未给出预期）",
        },
        "evidence": gate,
    }
