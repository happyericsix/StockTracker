"""策略审查：判"这份策略 JSON 对不对得上用户要的东西"。

<h3>为什么它一半是确定性的</h3>
`schema` 只保证**结构**合法。但引擎的语义里有一批"能通过校验、行为却完全不是你想要的"
写法 —— 它们不会报错，只会让回测**一笔都不成交**或**刚买就卖**，而用户看到的只是
"这策略没反应"。这些全部可以用算术判定，不需要模型：

| 写法 | 引擎实际会做什么 | 判定 |
|---|---|---|
| `stop_loss_pct: 8`（正数） | 条件是 `(close/entry-1)*100 <= 8` → **恒为真**，买入即卖出 | deterministic |
| `take_profit_pct: -5` | 条件是 `>= -5` → 恒为真，同样立刻出场 | deterministic |
| `lookback_days: 60` 配 `ma_cross slow=60` | 60 根 K 线算不出 MA60 → 条件恒为假，**永不成交** | deterministic |
| `lookback_days <= 20` | `run_backtest` 直接返回"数据不足" | deterministic |
| `logic=all` 同时要求 `rsi_above 70` 与 `rsi_below 30` | 逻辑上不可能同时成立 → 永不成交 | deterministic |
| `position.type=percent` 且 `size_pct=0` | 预算为 0 → 永远买不进 | deterministic |
| 同一条规则同时出现在 entry 与 exit | 疑似把入场规则抄进了出场 | deterministic |

剩下那一半（"用户说止损 8%、JSON 里却是 -5%"）才需要模型：**需求清单从自然语言里来**。

<h3>与 critic 同一套防幻觉机制</h3>
- **冷输入**：只给用户原话 + 策略 JSON + 已经算出来的结构问题，不给作者的解释；
- **没有工具**：它结构上改不了策略，只能出意见（改不改由调用方决定）；
- **每条判定必须指向真实存在的 JSON 路径**：路径是能**程序化验证**的，
  比"引用某个编号"更硬 —— 编一条不存在的路径，整条判定作废；
- **判决一致性**：列了 high 级问题却给 `accept`，会被确定性改成 `revise`；
- **fail-open**：模型不可用 / 输出不是 JSON → `unreviewed`，绝不阻断。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import llm_service
from agent.strategy_engine import compute_indicators  # noqa: F401  (文档锚点：语义约定来自这里)

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_UNREVIEWED = "unreviewed"

VERDICT_ACCEPT = "accept"
VERDICT_REVISE = "revise"
VERDICT_REJECT = "reject"
VERDICTS = (VERDICT_ACCEPT, VERDICT_REVISE, VERDICT_REJECT)

SATISFIED = "satisfied"
VIOLATED = "violated"
NOT_STATED = "not_stated"
UNCLEAR = "unclear"
REQUIREMENT_STATUSES = (SATISFIED, VIOLATED, NOT_STATED, UNCLEAR)

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

# 引擎的硬门槛：run_backtest 在 len(records) <= 20 时直接判"数据不足"
MIN_BACKTEST_BARS = 21
# MACD 需要 26 + 9 根才能出第一根有效值；RSI 需要 14 + 1
MACD_BARS = 35
RSI_BARS = 15

REVIEW_TEMPERATURE = 0.0
REVIEW_MAX_TOKENS = 1200
MAX_REQUIREMENTS = 10

STRATEGY_REVIEW_PROMPT = """你是**策略审查者**。用户用自然语言描述了一个交易策略，模型把它转成了\
策略 JSON。你的任务是判断：**这份 JSON 有没有如实实现用户要的东西**。

你会看到：
1. `user_request`：用户的原话。
2. `strategy_json`：生成出来的策略配置。
3. `structural_findings`：系统已经用算术查出来的问题（例如止损写成了正数、\
回看窗口不足以算出所用均线）。**这些不用你重复判断**，你只需要在评估需求时把它们当作已知事实。

请按两步做：

第一步，列出用户**明确要求**的要点（`requirements`）。只列原话里真的有的，不要补充常识：
- 用户说"20日均线上穿60日买入" → 一条入场要求；
- 用户说"止损 8%" → 一条风险要求，数值是 8%；
- 用户没提仓位 → **不要**编一条仓位要求出来。

第二步，逐条对照 `strategy_json` 判定：
- satisfied：JSON 里有对应的规则，且数值/方向一致；
- violated：JSON 里没有，或者数值/方向对不上（说 8% 写成 5%、说"跌破卖出"却配了止盈）；
- not_stated：用户没说，JSON 里也没有 —— 这不算问题；
- unclear：JSON 里的写法与用户原话之间的关系无法确定。

然后只输出 JSON：
{
  "requirements": [
    {"text": "止损 8%", "status": "violated",
     "path": "exit.conditions[1].value", "note": "JSON 里是 -5，与用户说的 8% 不一致"}
  ],
  "findings": [
    {"kind": "requirement_mismatch", "severity": "high",
     "detail": "用户要求止损 8%，JSON 是 5%", "path": "exit.conditions[1].value"}
  ],
  "verdict": "revise",
  "summary": "一句话"
}

规则（必须遵守）：
- **每条 finding 与每一条 violated/unclear 的 requirement 都必须带 `path`**，\
且路径必须真实存在于 `strategy_json` 里（例如 `entry.conditions[0].slow`）。\
编一条不存在的路径 = 这条判定作废。
- 不要使用你自己的市场知识去评价这个策略好不好 —— 你只判断"有没有如实实现用户的要求"。
- 不要重复 structural_findings 里已经列出的问题。
- `verdict` 只能是 accept / revise / reject。列了 high 级问题时不能给 accept。
- 只输出 JSON，不要解释、不要代码块以外的任何文字。"""


# ==================== 确定性半边：语义与算术 ====================


def _iter_conditions(config: dict):
    """产出 (path, group, index, condition)，path 形如 `entry.conditions[0]`。"""
    for group in ("entry", "exit"):
        rule = config.get(group) if isinstance(config, dict) else None
        conditions = (rule or {}).get("conditions") if isinstance(rule, dict) else None
        for index, condition in enumerate(conditions or []):
            if isinstance(condition, dict):
                yield f"{group}.conditions[{index}]", group, index, condition


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def required_bars(config: dict) -> int:
    """这份策略至少要多少根 K 线才可能成交（引擎的指标窗口决定）。"""
    needed = MIN_BACKTEST_BARS
    for _path, _group, _index, condition in _iter_conditions(config):
        ctype = str(condition.get("type") or "")
        if ctype == "ma_cross":
            slow = _num(condition.get("slow")) or 0
            # 交叉需要"上一根"与"这一根"，所以是 slow + 1
            needed = max(needed, int(slow) + 1)
        elif ctype == "macd_cross":
            needed = max(needed, MACD_BARS)
        elif ctype in ("rsi_above", "rsi_below"):
            needed = max(needed, RSI_BARS)
    return needed


def structural_findings(config: dict) -> list[dict]:
    """纯算术/语义检查，零成本。返回问题清单（每条带可验证的 JSON 路径）。"""
    findings = []
    if not isinstance(config, dict):
        return findings

    def add(kind, severity, detail, path):
        findings.append({"kind": kind, "severity": severity, "detail": detail, "path": path})

    data = config.get("data") if isinstance(config.get("data"), dict) else {}
    lookback = _num(data.get("lookback_days"))
    needed = required_bars(config)

    if lookback is not None:
        if lookback < MIN_BACKTEST_BARS:
            add("insufficient_lookback", SEVERITY_HIGH,
                f"回看窗口只有 {int(lookback)} 根 K 线，回测会直接判定数据不足"
                f"（至少需要 {MIN_BACKTEST_BARS} 根）", "data.lookback_days")
        elif lookback < needed:
            add("insufficient_lookback", SEVERITY_HIGH,
                f"回看窗口 {int(lookback)} 根不足以算出所用的指标（需要 {needed} 根）——"
                f"条件恒为假，回测一笔都不会成交", "data.lookback_days")
        elif lookback < needed * 1.5:
            add("tight_lookback", SEVERITY_LOW,
                f"回看窗口 {int(lookback)} 根对所需 {needed} 根只多一点余量，"
                f"样本太少、回测结论不稳", "data.lookback_days")

    for path, _group, _index, condition in _iter_conditions(config):
        ctype = str(condition.get("type") or "")
        value = _num(condition.get("value"))
        if ctype == "stop_loss_pct" and value is not None and value >= 0:
            add("sign_flipped_condition", SEVERITY_HIGH,
                f"止损写成了 {value}（应为负数）：引擎判据是 (现价/成本-1)*100 <= value，"
                f"正数会让它**恒为真**，买入当根即触发卖出", f"{path}.value")
        if ctype == "take_profit_pct" and value is not None and value <= 0:
            add("sign_flipped_condition", SEVERITY_HIGH,
                f"止盈写成了 {value}（应为正数）：引擎判据是 >= value，"
                f"非正数会让它恒为真", f"{path}.value")
        if ctype == "trailing_stop_pct" and value is not None and value <= 0:
            add("sign_flipped_condition", SEVERITY_HIGH,
                f"跟踪止盈写成了 {value}（应为正数）：引擎判据是最大回撤幅度 >= value",
                f"{path}.value")
        if ctype == "rsi_above" and value is not None and value > 100:
            add("impossible_condition", SEVERITY_MEDIUM,
                f"RSI 不可能超过 100，rsi_above {value} 永远不成立", f"{path}.value")
        if ctype == "rsi_below" and value is not None and value < 0:
            add("impossible_condition", SEVERITY_MEDIUM,
                f"RSI 不可能小于 0，rsi_below {value} 永远不成立", f"{path}.value")
        if ctype == "ma_cross":
            fast, slow = _num(condition.get("fast")), _num(condition.get("slow"))
            if fast is not None and slow is not None and fast >= slow:
                add("inverted_ma_cross", SEVERITY_MEDIUM,
                    f"快线 {int(fast)} 不短于慢线 {int(slow)}：交叉方向会与直觉相反",
                    f"{path}.fast")

    # 逻辑上永远为假的组合（logic=all 同时要求互斥区间）
    for group in ("entry", "exit"):
        rule = config.get(group) if isinstance(config.get(group), dict) else {}
        if rule.get("logic") != "all":
            continue
        conditions = [c for c in (rule.get("conditions") or []) if isinstance(c, dict)]
        above = [(_num(c.get("value")), c) for c in conditions
                 if c.get("type") in ("rsi_above", "price_above")]
        below = [(_num(c.get("value")), c) for c in conditions
                 if c.get("type") in ("rsi_below", "price_below")]
        for upper, _c1 in above:
            for lower, _c2 in below:
                if upper is None or lower is None:
                    continue
                if lower <= upper:
                    add("impossible_combination", SEVERITY_HIGH,
                        f"{group}.logic=all 同时要求「高于 {upper}」与「低于等于 {lower}」，"
                        f"永远不会同时成立 —— 该规则永不触发",
                        f"{group}.logic")
                    break

    # 同一条规则同时出现在入场与出场（多半是把入场条件抄进了出场）
    def signature(condition):
        return json.dumps({k: condition.get(k) for k in ("type", "fast", "slow", "direction", "value")},
                          sort_keys=True)

    entry_sigs = {signature(c): path for path, group, _i, c in _iter_conditions(config)
                  if group == "entry"}
    for path, group, _index, condition in _iter_conditions(config):
        if group == "exit" and signature(condition) in entry_sigs:
            add("duplicate_condition", SEVERITY_MEDIUM,
                f"这条规则与入场规则完全相同（入场 {entry_sigs[signature(condition)]}）："
                f"疑似把入场条件抄进了出场", path)

    position = config.get("position") if isinstance(config.get("position"), dict) else {}
    if position.get("type") == "percent":
        size = _num(position.get("size_pct"))
        if size is None or size <= 0 or size > 100:
            add("invalid_position_size", SEVERITY_HIGH,
                f"仓位比例 {size} 不在 (0, 100] 内：预算算出来是 0 或超额，"
                f"要么买不进、要么下不出单", "position.size_pct")

    capital = _num(config.get("initial_capital"))
    if capital is not None and capital <= 0:
        add("invalid_capital", SEVERITY_MEDIUM,
            f"初始资金 {capital} 非正数，回测没有可交易的资金", "initial_capital")

    return findings


# ==================== 路径校验（比"引用编号"更硬的那种证据） ====================


def _path_exists(config, path: str) -> bool:
    """验证 `entry.conditions[0].slow` 这类路径在 JSON 里真的存在。

    这是这套审查的证据机制：**引用不存在的路径 = 整条判定作废**。
    路径可以被程序化验证，所以"编一条证据"这件事在这里不可能成立。
    """
    if not isinstance(path, str) or not path.strip():
        return False
    current = config
    for token in path.strip().replace("[", ".[").split("."):
        if not token:
            continue
        match = re.fullmatch(r"(\w+)?\[(\d+)\]", token)
        if match:
            name, index = match.group(1), int(match.group(2))
            if name:
                if not isinstance(current, dict) or name not in current:
                    return False
                current = current[name]
            if not isinstance(current, list) or index >= len(current):
                return False
            current = current[index]
        else:
            if not isinstance(current, dict) or token not in current:
                return False
            current = current[token]
    return True


def _extract_json(text) -> Optional[dict]:
    """与 review.py 同一套容错（这里独立一份，避免两个模块互相耦合）。"""
    if not isinstance(text, str):
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    start, end = candidate.find("{"), candidate.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(candidate[start:end + 1])
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _filter_findings(raw_findings, config) -> tuple[list, list]:
    """保留路径真实存在的判定；其余作废（并记下来，便于观察模型编路径的频率）。"""
    kept, dropped = [], []
    for item in raw_findings or ():
        if not isinstance(item, dict):
            dropped.append({"reason": "not_an_object"})
            continue
        path = str(item.get("path") or "").strip()
        severity = str(item.get("severity") or "").strip().lower()
        if not _path_exists(config, path):
            dropped.append({"reason": "unknown_path", "path": path})
            continue
        if severity not in (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW):
            severity = SEVERITY_MEDIUM
        kept.append({
            "kind": str(item.get("kind") or "requirement_mismatch").strip(),
            "severity": severity,
            "detail": str(item.get("detail") or "").strip(),
            "path": path,
        })
    return kept, dropped


def _filter_requirements(raw_requirements, config) -> list:
    """需求清单：只保留状态合法、且 violated/unclear 的必定带真实路径的条目。"""
    kept = []
    for item in raw_requirements or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        if not text or status not in REQUIREMENT_STATUSES:
            continue
        path = str(item.get("path") or "").strip()
        if status in (VIOLATED, UNCLEAR) and not _path_exists(config, path):
            # 说"没实现"却指不出位置 —— 这种指控不能采信
            continue
        kept.append({"text": text, "status": status, "path": path,
                     "note": str(item.get("note") or "").strip()})
        if len(kept) >= MAX_REQUIREMENTS:
            break
    return kept


def _resolve_verdict(raw_verdict: str, findings: list) -> str:
    """判决一致性：列了 high 级问题却给 accept，确定性改成 revise。

    为什么不让模型自己保证：这正是"判自己"的地方。放过的代价是用户拿着
    一个止损方向写反的策略去跑模拟盘。
    """
    verdict = str(raw_verdict or "").strip().lower()
    if verdict not in VERDICTS:
        verdict = VERDICT_REVISE
    has_high = any(item.get("severity") == SEVERITY_HIGH for item in findings or [])
    if has_high and verdict == VERDICT_ACCEPT:
        logger.info("策略审查：列了 high 级问题却判 accept，已改判 revise")
        return VERDICT_REVISE
    return verdict


def review_strategy(user_request: str, strategy_json, *, completion=None) -> dict:
    """审查一份策略 JSON：先做确定性检查，再（有用户原话时）做需求比对。

    Args:
        user_request: 用户原话。为空则只做确定性检查（零成本）。
        strategy_json: 策略配置 dict。
        completion: 便于测试注入；缺省走 `llm_service.chat_completion`。

    永不抛异常：任何异常都退化成 `status=unreviewed`，只带上确定性那一半的结论。
    """
    try:
        structural = structural_findings(strategy_json)
        base = {
            "status": STATUS_OK,
            "structural_findings": structural,
            "requirements": [],
            "findings": structural,
            "dropped": [],
            "verdict": _resolve_verdict(None, structural),
            "summary": "",
            "cost": {"llm_calls": 0},
        }
        if not isinstance(strategy_json, dict) or not str(user_request or "").strip():
            # 没有原话就没有"对不对得上"可言 —— 不花这次钱
            base["summary"] = ("没有用户原话，只做了确定性检查" if isinstance(strategy_json, dict)
                               else "没有可审查的策略")
            return base

        payload = {
            "user_request": user_request,
            "strategy_json": strategy_json,
            "structural_findings": structural,
        }
        messages = [
            {"role": "system", "content": STRATEGY_REVIEW_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)
             + "\n\n只输出 JSON。"},
        ]
        call = completion or llm_service.chat_completion
        choice = call(messages, temperature=REVIEW_TEMPERATURE, max_tokens=REVIEW_MAX_TOKENS)
        message = (choice or {}).get("message") if isinstance(choice, dict) else None
        content = (message or {}).get("content") if isinstance(message, dict) else None
        parsed = _extract_json(content)
        if parsed is None:
            logger.info("策略审查输出不是合法 JSON，按未审查处理: %s", str(content)[:200])
            base["status"] = STATUS_UNREVIEWED
            base["reason"] = "invalid_json"
            base["cost"] = {"llm_calls": 1}
            return base

        semantic, dropped = _filter_findings(parsed.get("findings"), strategy_json)
        requirements = _filter_requirements(parsed.get("requirements"), strategy_json)
        findings = structural + semantic
        base.update({
            "semantic_findings": semantic,
            "findings": findings,
            "requirements": requirements,
            "dropped": dropped,
            "verdict": _resolve_verdict(parsed.get("verdict"), findings),
            "summary": str(parsed.get("summary") or "").strip(),
            "cost": {"llm_calls": 1},
        })
        return base
    except Exception as exc:  # noqa: BLE001 —— 审查是旁路，绝不影响一轮对话
        logger.warning("策略审查失败: %s", exc)
        return {"status": STATUS_UNREVIEWED, "reason": str(exc), "structural_findings": [],
                "requirements": [], "findings": [], "dropped": [],
                "verdict": VERDICT_REVISE, "summary": "", "cost": {"llm_calls": 1}}


def describe() -> dict:
    """给 /health 与调试用：这一半确定性查什么、那一半模型管什么。"""
    return {
        "deterministic_checks": ["sign_flipped_condition", "insufficient_lookback",
                                 "tight_lookback", "impossible_condition",
                                 "impossible_combination", "inverted_ma_cross",
                                 "duplicate_condition", "invalid_position_size",
                                 "invalid_capital"],
        "semantic_checks": ["requirement_mismatch"],
        "evidence": "json_path",
        "tools": [],
        "blocking": False,
        "verdicts": list(VERDICTS),
        "min_backtest_bars": MIN_BACKTEST_BARS,
    }
