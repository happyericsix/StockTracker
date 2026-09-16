"""一轮对话结束后的**确定性体检**（审计）。

<h3>它回答什么问题</h3>
"这一轮到底正不正常" —— 拆成四类**可判定**的问题，全部不需要再调一次模型：

1. **数字溯源**：回答里的数字，能不能在某次工具调用的结果里找到依据？
   说"最大回撤 18.3%"却没有任何一次成功调用给出过 18.3 —— 这是最危险的一类错
   （看起来最专业的那句话往往是编的），而它**可以被机械地查出来**。
2. **序列异常**：对着已判定不可用的数据源反复重试（系统提示明令禁止，但从来没人检查它
   是否遵守）、同一工具同参重复调用（浪费一步 LLM）、结果被裁剪后照用。
3. **策略层拒绝**：`policy_denied` / `budget_exceeded` / `needs_approval` 的出现次数 ——
   拒绝率高通常不是模型的问题，而是**工具描述或装填的问题**（模型看见了一个它调不动的工具）。
4. **合规**：不承诺收益、不预测点位。这一层必须是确定性的：把它交给模型自觉，
   等于把唯一一条不能出错的规则交给了最不确定的部件。

<h3>为什么不做成"评分"</h3>
评分是可以用来自欺的（0.87 分是什么意思？）。这里只产出**问题清单**，
每条带 `kind` / `severity` / `detail` / `evidence`，可断言、可聚合、可进 CI。
`verdict` 只有两个值：`clean` 与 `review`（有 medium 及以上问题）。

<h3>它绝不阻断任何流程</h3>
审计是旁路：出问题只记日志，绝不让一轮对话失败 —— 与 `metering` / 记忆系统同一条纪律。
调用方（`react_agent.run_agent`）把它放进结果里，同时打一条 warning。

<h3>低召回、高精度是刻意的</h3>
数字溯源只审"看起来像事实断言"的数字（带 `%`、带单位、或量级 ≥ 100、或有小数位），
并用 1% 容差容忍"约 1500 元"这类合法近似。误报会迅速摧毁这套审计的可信度，
而漏报的代价只是少发现一条问题 —— 所以宁可漏，不可冤。需要全量报告时传 `strict=True`。
"""
from __future__ import annotations

import json
import logging
import re
from typing import Iterable, Optional

from agent import tool_contract as tc
from agent.tool_scope import ToolBudget

logger = logging.getLogger(__name__)

# ==================== 严重度与问题类型 ====================

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"

KIND_UNSUPPORTED_CLAIM = "unsupported_claim"
KIND_RETRY_AFTER_UNAVAILABLE = "retry_after_source_unavailable"
KIND_REPEATED_CALL = "repeated_call"
KIND_TRUNCATED_RESULT = "truncated_result_used"
KIND_BUDGET_PRESSURE = "budget_pressure"
KIND_REFUSALS = "policy_refusals"
KIND_TOOL_FAILURES = "tool_failures"
KIND_COMPLIANCE = "compliance"

# 近似复述的容差：模型说"约 1500 元"而工具给的是 1498.5，这是正常的表达，不是编造。
# 只对量级 ≥ 100 的数生效（小数用它自己的显示精度比对，见 _matches）。
APPROX_TOLERANCE = 0.01
APPROX_MIN_MAGNITUDE = 100.0

# 合规黑名单：承诺收益与预测点位。只放"几乎不可能是正常表述"的词，
# 避免把"注意不要满仓"这类风控提醒也打成违规。
FORBIDDEN_PATTERNS = (
    r"必涨", r"必跌", r"稳赚", r"包赚", r"保证收益", r"保本保息",
    r"一定涨", r"一定跌", r"肯定涨", r"肯定跌",
    r"预测涨到", r"预测跌到", r"明天会涨", r"明天会跌",
)

# 号码类噪声：日期、时间、A 股代码 —— 它们不是"事实断言"
_DATE_OR_TIME_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?)?|\d{1,2}:\d{2}(?::\d{2})?")
# 6 位数字里只有 A 股代码的首位集合（0/3/4/6/8）才算代码。
# 这一条不是抠细节：`100000`（初始资金）也是 6 位，按"任意 6 位数字"处理会把它
# 从审计里悄悄摘掉 —— 于是"编了一个资金数字"这件事永远查不出来。
_STOCK_CODE_RE = re.compile(r"(?<!\d)[03468]\d{5}(?!\d)")
_NUMBER_RE = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")

# 单位/标记：带这些的数字几乎一定是事实断言
_CLAIM_UNITS = ("%", "％", "元", "块", "万", "亿", "倍", "点", "手", "股")
# 允许按 10 的幂换算的单位词（A 股惯例：成交额常用万元、市值常用亿元）。
# 没有单位词的数字不享受这条宽容 —— 否则差 10 倍的错会被放过。
_SCALABLE_UNITS = ("元", "块", "万", "亿", "手", "股")
_UNIT_SCALES = (1e-8, 1e-4, 1e-3, 1e-2, 1e2, 1e3, 1e4, 1e8)
# 指标词：出现在数字附近即视为断言（"回撤 18"）
_METRIC_WORDS = ("回撤", "收益", "胜率", "夏普", "涨", "跌", "价", "成交", "量", "额", "率",
                 "净值", "市值", "市盈", "利润", "营收", "波动", "仓位", "成本")

# 账本 meta 里可能出现的失败码（取不到 meta.code 时的兜底，兼容历史行）
_KNOWN_CODES = (tc.SOURCE_UNAVAILABLE, tc.POLICY_DENIED, tc.BUDGET_EXCEEDED,
                tc.NEEDS_APPROVAL, tc.INVALID_ARGS, tc.TOOL_TIMEOUT, tc.TOOL_BUSY,
                tc.INSUFFICIENT_DATA, tc.UNKNOWN_TOOL, tc.TOOL_ERROR)


# ==================== 数字提取与比对 ====================


def _strip_noise(text: str) -> str:
    """把日期/时间/股票代码从文本里挖掉，避免它们被当成事实断言。"""
    text = _DATE_OR_TIME_RE.sub(" ", text or "")
    return _STOCK_CODE_RE.sub(" ", text)


def extract_numbers(text: str) -> list[dict]:
    """抽出文本里的数字，带原始写法与显示精度。

    `decimals` 是**原样写法里的小数位数** —— 比对时要按它舍入：
    工具给 18.34、模型写 "18.3%"，这是合法的呈现，不是编造。
    """
    found = []
    for match in _NUMBER_RE.finditer(_strip_noise(text)):
        raw = match.group(0)
        try:
            value = float(raw.replace(",", ""))
        except ValueError:
            continue
        decimals = len(raw.split(".")[1]) if "." in raw else 0
        found.append({"raw": raw, "value": value, "decimals": decimals,
                      "start": match.start(), "end": match.end()})
    return found


def _window(text: str, item: dict, *, before: int = 12, after: int = 3) -> str:
    """数字周围的一小段文字（判"它像不像在陈述事实"、以及"有没有单位词"都用它）。"""
    return text[max(0, item["start"] - before): item["end"] + after]


def _claim_like(text: str, item: dict, strict: bool) -> bool:
    """这个数字像不像"在陈述一个事实"。

    低召回是刻意的：序号、条数、参数（20 日均线）都不该被审。只有
    "带单位/百分号"、"量级够大"、"带小数位"或"附近有指标词"的才算。
    """
    if strict:
        return True
    value = abs(item["value"])
    if item["decimals"] > 0:
        return True
    if any(unit in _window(text, item) for unit in _CLAIM_UNITS):
        return True
    if value >= APPROX_MIN_MAGNITUDE:
        return True
    nearby = _window(text, item, before=12, after=12)
    return any(word in nearby for word in _METRIC_WORDS)


def _matches(value: float, decimals: int, support: float) -> bool:
    """模型写的这个数字，能不能由 support 里某个值支撑。"""
    if abs(value - support) < 1e-9:
        return True
    # 符号由文字承担：工具给 涨跌额=-10.88，模型写"跌 10.88 元"是正确用法。
    # （真实一轮里抓到过这一条：不加这个规则，"跌 10.88 元"会被判成编造。）
    if abs(abs(value) - abs(support)) < 1e-9:
        return True
    # 按模型的显示精度舍入后相等：18.34 → "18.3" 属于合法呈现
    if round(support, decimals) == round(value, decimals):
        return True
    # 近似复述："约 1500 元" 对应 1498.5。只对量级 ≥ 100 的数生效。
    if abs(value) >= APPROX_MIN_MAGNITUDE and support:
        return abs(value - support) / abs(support) <= APPROX_TOLERANCE
    return False


def _unit_scales_supported(value: float, decimals: int, support_values: list) -> bool:
    """A 股数据里最常见的合法推导：**单位换算**。

    真实一轮里抓到过：行情工具给的成交额是 `2398035`（万元），模型写成
    "成交额约 239.8 亿元" —— 这是对的（2398035 万 = 239.8035 亿），
    而按"字面必须出现"的规则它会被判成编造。

    所以只在**文本里带单位词**时才允许按 10 的幂换算（元/万/亿/手/股），
    否则一个差 10 倍的错会被悄悄放过。
    """
    if not support_values:
        return False
    for scale in _UNIT_SCALES:
        scaled = value / scale
        for support in support_values:
            if _matches(scaled, decimals, support):
                return True
    return False


def content_texts(tool_contents) -> list[str]:
    """把"模型看到过的工具结果"归一成字符串列表（支持 dict / str 两种形态）。"""
    texts = []
    for item in tool_contents or ():
        if isinstance(item, dict):
            texts.append(str(item.get("content") or ""))
        elif isinstance(item, str):
            texts.append(item)
    return texts


def _support_values(texts: Iterable[str]) -> list[float]:
    values = []
    for text in texts or ():
        if not isinstance(text, str):
            continue
        values.extend(item["value"] for item in extract_numbers(text))
    return values


def unsupported_claims(replies, support_texts, *, strict: bool = False) -> list[dict]:
    """回答里那些**找不到依据**的数字。

    `support_texts` 应当包含"模型这一轮真实看到过的内容"：工具结果、
    记忆注入块、用户原话、已产出的策略 JSON。**刻意不包含 system prompt** ——
    提示词里的 schema 示例（-8、100000、250）如果算作依据，
    模型抄一个示例数字就永远"有据"了，那等于把这条检查废掉。
    """
    support = _support_values(support_texts)
    out = []
    for reply in replies or ():
        if not isinstance(reply, str):
            continue
        for item in extract_numbers(reply):
            if not _claim_like(reply, item, strict):
                continue
            if any(_matches(item["value"], item["decimals"], value) for value in support):
                continue
            # 带单位词的数字允许一次单位换算（"成交额 239.8 亿元" ↔ 工具给的 2398035 万元）
            window = _window(reply, item)
            if (any(unit in window for unit in _SCALABLE_UNITS)
                    and _unit_scales_supported(item["value"], item["decimals"], support)):
                continue
            # 带上上下文片段：没有它，事后判断"这是编造还是合法推导"必须重跑一轮
            # （真实 triage 时踩过这个坑：只看到 "16.2"，无法判断它从哪来）
            out.append({"number": item["raw"], "value": item["value"],
                        "context": _window(reply, item, before=14, after=8).replace("\n", " ").strip()})
    return out


# ==================== 序列异常 ====================


def _meta_of(entry) -> dict:
    """账本条目里的 meta（可能是 JSON 字符串，也可能已经是 dict）。解析不了就返回空。"""
    if not isinstance(entry, dict):
        return {}
    meta = entry.get("meta")
    if isinstance(meta, dict):
        return meta
    if isinstance(meta, str):
        try:
            parsed = json.loads(meta)
        except (json.JSONDecodeError, TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _code_of(entry, meta: dict) -> str:
    """这次调用的错误码：优先取 meta.code，兜底从历史行的文本里认出已知码。"""
    code = str(meta.get("code") or "").strip()
    if code:
        return code
    content = str(entry.get("content") or "") if isinstance(entry, dict) else ""
    for known in _KNOWN_CODES:
        if known in content:
            return known
    return ""


def _call_signature(meta: dict) -> str:
    args = meta.get("args")
    try:
        return json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(args)


def tool_calls(tool_log) -> list[dict]:
    """把账本条目整理成"这一轮调了什么"的结构化序列（审计与测试共用）。"""
    calls = []
    for entry in tool_log or ():
        meta = _meta_of(entry)
        name = str(meta.get("tool") or "").strip()
        if not name:
            continue
        calls.append({
            "tool": name,
            "ok": bool(meta.get("ok")),
            "code": _code_of(entry, meta),
            "role": meta.get("role"),
            "signature": _call_signature(meta),
            "content": str(entry.get("content") or "") if isinstance(entry, dict) else "",
        })
    return calls


def sequence_findings(calls: list[dict], tool_contents=None) -> list[dict]:
    """从调用序列里找异常。全部基于**顺序**与**重复**，不需要模型参与。

    `tool_contents` 是模型实际看到的工具结果文本：裁剪标记（`truncated`）只存在于
    那里，账本条目里只有一条摘要 —— 所以这个检查必须看内容，不能看账本。
    """
    findings = []

    # 1) 对着已判定不可用的源反复重试（提示词明令禁止，这里检查它有没有遵守）
    unavailable_at: dict[str, int] = {}
    for index, call in enumerate(calls):
        if call["code"] == tc.SOURCE_UNAVAILABLE:
            unavailable_at.setdefault(call["tool"], index)
    for index, call in enumerate(calls):
        first = unavailable_at.get(call["tool"])
        if first is not None and index > first:
            findings.append({
                "kind": KIND_RETRY_AFTER_UNAVAILABLE,
                "severity": SEVERITY_HIGH,
                "detail": f"{call['tool']} 在判定源不可用后又被调了一次（第 {first + 1} → {index + 1} 次）",
                "evidence": call["tool"],
            })
            break  # 同一工具报一次就够，不刷屏

    # 2) 同一工具同参重复调用：管道里的同轮缓存会命中，但模型已经白花了一步
    seen: dict[tuple, int] = {}
    for index, call in enumerate(calls):
        key = (call["tool"], call["signature"])
        if key in seen:
            findings.append({
                "kind": KIND_REPEATED_CALL,
                "severity": SEVERITY_LOW,
                "detail": f"{call['tool']} 用同样的参数调了两次（第 {seen[key] + 1} 与第 {index + 1} 次）",
                "evidence": call["tool"],
            })
            break
        seen[key] = index

    # 3) 结果被裁剪过：结论可能是基于不完整的数据下的
    for text in content_texts(tool_contents):
        if '"truncated": true' in text or "'truncated': True" in text:
            findings.append({
                "kind": KIND_TRUNCATED_RESULT,
                "severity": SEVERITY_LOW,
                "detail": "本轮有工具结果被裁剪过，结论可能只基于部分数据",
                "evidence": "truncated",
            })
            break

    # 4) 失败次数：单次失败是正常的（模型会改做法），连着失败说明它在原地打转
    failures = [call for call in calls if not call["ok"]]
    tool_failures = [call for call in failures
                     if call["code"] not in (tc.POLICY_DENIED, tc.BUDGET_EXCEEDED,
                                             tc.NEEDS_APPROVAL)]
    if len(tool_failures) >= 2:
        codes = sorted({call["code"] or "unknown" for call in tool_failures})
        findings.append({
            "kind": KIND_TOOL_FAILURES,
            "severity": SEVERITY_MEDIUM,
            "detail": f"本轮有 {len(tool_failures)} 次工具失败：{', '.join(codes)}",
            "evidence": ",".join(sorted({call["tool"] for call in tool_failures})),
        })
    return findings


def refusal_findings(calls: list[dict]) -> list[dict]:
    """策略层拒绝：拒得多，通常说明工具**描述或装填**有问题，而不是模型不听话。"""
    refused = [call for call in calls
               if call["code"] in (tc.POLICY_DENIED, tc.BUDGET_EXCEEDED, tc.NEEDS_APPROVAL)]
    if not refused:
        return []
    by_code: dict[str, int] = {}
    for call in refused:
        by_code[call["code"]] = by_code.get(call["code"], 0) + 1
    detail = "、".join(f"{code}×{count}" for code, count in sorted(by_code.items()))
    return [{
        "kind": KIND_REFUSALS,
        "severity": SEVERITY_MEDIUM if len(refused) > 1 else SEVERITY_LOW,
        "detail": f"本轮有 {len(refused)} 次被策略层拒绝（{detail}）—— "
                  f"模型看见了一个它调不动的工具，先看描述与装填",
        "evidence": ",".join(sorted({call["tool"] for call in refused})),
    }]


def budget_findings(calls: list[dict], budget: Optional[dict] = None) -> list[dict]:
    """预算压力：这一轮几乎用满了配额 → 说明这个任务该拆角色，而不是加大配额。"""
    limits = budget or {}
    max_calls = int(limits.get("max_calls_per_turn") or ToolBudget().max_calls_per_turn)
    if max_calls <= 0 or len(calls) < max_calls * 0.75:
        return []
    return [{
        "kind": KIND_BUDGET_PRESSURE,
        "severity": SEVERITY_MEDIUM,
        "detail": f"本轮工具调用 {len(calls)}/{max_calls}，接近配额上限 —— "
                  f"这类任务该按角色拆（见 tool_sets.MODES），而不是调大配额",
        "evidence": f"calls={len(calls)}",
    }]


# ==================== 合规 ====================


def compliance_findings(replies) -> list[dict]:
    """承诺收益 / 预测点位：确定性的黑名单检查，不交给模型自觉。"""
    out = []
    for reply in replies or ():
        if not isinstance(reply, str):
            continue
        for pattern in FORBIDDEN_PATTERNS:
            match = re.search(pattern, reply)
            if match:
                out.append({
                    "kind": KIND_COMPLIANCE,
                    "severity": SEVERITY_HIGH,
                    "detail": f"出现违规表述「{match.group(0)}」",
                    "evidence": match.group(0),
                })
    return out


# ==================== 入口 ====================


def audit_turn(replies=None, tool_log=None, tool_contents=None, *,
               user_message: str = "", context_block: str = "",
               strategy_json=None, budget: Optional[dict] = None,
               strict: bool = False) -> dict:
    """体检一轮对话，返回问题清单（永不抛异常）。

    Args:
        replies: 这一轮发出去的回复文本
        tool_log: `react_agent` 的账本条目（含 meta：tool / ok / code / args / role）
        tool_contents: 模型**实际看到**的工具结果文本（数字溯源的依据）
        user_message: 用户原话（用户自己说过的数字不算编造）
        context_block: 注入的记忆块（用户之前说过的数字同样不算编造）
        strategy_json: 本轮产出的策略 JSON（它的参数是被校验过的，算依据）
        budget: 可选的配额快照；缺省用 `ToolBudget` 的默认上限
        strict: True 时不做"像不像断言"的过滤，报告所有找不到依据的数字

    Returns:
        {"verdict", "calls", "ok", "failed", "refused", "unsupported_numbers",
         "findings", "checked"}
    """
    try:
        calls = tool_calls(tool_log)
        seen_texts = content_texts(tool_contents)
        support = [user_message or "", context_block or ""] + seen_texts
        if strategy_json is not None:
            try:
                support.append(json.dumps(strategy_json, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                pass

        findings = []
        unsupported = unsupported_claims(replies, support, strict=strict)
        if unsupported:
            numbers = "、".join(item["number"] for item in unsupported[:5])
            contexts = " ｜ ".join(f"…{item['context']}…" for item in unsupported[:3])
            findings.append({
                "kind": KIND_UNSUPPORTED_CLAIM,
                "severity": SEVERITY_MEDIUM,
                "detail": f"回答里的这些数字找不到依据：{numbers}（上下文：{contexts}）",
                "evidence": numbers,
            })
        findings.extend(compliance_findings(replies))
        findings.extend(sequence_findings(calls, seen_texts))
        findings.extend(refusal_findings(calls))
        findings.extend(budget_findings(calls, budget))

        severity_rank = {SEVERITY_HIGH: 3, SEVERITY_MEDIUM: 2, SEVERITY_LOW: 1}
        worst = max((severity_rank.get(f["severity"], 0) for f in findings), default=0)
        return {
            "verdict": "review" if worst >= severity_rank[SEVERITY_MEDIUM] else "clean",
            "calls": len(calls),
            "ok": sum(1 for call in calls if call["ok"]),
            "failed": sum(1 for call in calls if not call["ok"]),
            "refused": sum(1 for call in calls if call["code"] in
                           (tc.POLICY_DENIED, tc.BUDGET_EXCEEDED, tc.NEEDS_APPROVAL)),
            "unsupported_numbers": [item["number"] for item in unsupported],
            # 结构化候选：critic（agent/review.py）直接消费它，不必去解析 detail 文本
            "unsupported_claims": unsupported,
            "findings": findings,
            "checked": ["numbers", "compliance", "sequence", "refusals", "budget"],
        }
    except Exception as exc:  # noqa: BLE001 —— 审计是旁路，绝不能影响一轮对话
        logger.debug("审计失败: %s", exc)
        return {"verdict": "unknown", "calls": 0, "ok": 0, "failed": 0, "refused": 0,
                "unsupported_numbers": [], "findings": [], "checked": [],
                "error": str(exc)}


def describe() -> dict:
    """给 /health 与调试用：这套审计查什么、阈值是多少。"""
    return {
        "checks": ["unsupported_claim", "compliance", "retry_after_source_unavailable",
                   "repeated_call", "truncated_result", "tool_failures",
                   "policy_refusals", "budget_pressure"],
        "approx_tolerance": APPROX_TOLERANCE,
        "approx_min_magnitude": APPROX_MIN_MAGNITUDE,
        "forbidden_patterns": list(FORBIDDEN_PATTERNS),
        "blocking": False,
    }
