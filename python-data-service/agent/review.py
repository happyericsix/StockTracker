"""裁决者（critic）：把确定性审计产出的**候选**判成"合法推导"或"编造"。

<h3>为什么需要它（以及为什么它不该做别的事）</h3>
`agent/tool_audit.py` 能用零成本查出"回答里的数字找不到字面依据"，但它的精度上限
卡在一个地方：**合法推导的种类是无穷的** —— 单位换算（2398035 万元 → 239.8 亿元）、
差值、求和、比例、四舍五入、符号由文字承担（"跌 10.88 元" ↔ 涨跌额 -10.88）……
每加一条宽容规则，就多放过一类真错。

所以这里的分工是死的：

    确定性审计 → 产出**候选**（带上下文片段与证据原文）
    critic     → 只做**裁决**：这个数字是推出来的，还是编的

critic 拿到的是**冷输入**：候选数字、它的上下文片段、以及作者当时**实际看到**的
工具结果原文（编号 E1、E2…）。它看不到作者的推理过程 —— 否则就变成同一段上下文里的
自我确认，那是最没价值的一种"审查"。

<h3>三条防幻觉的硬约束</h3>
1. **判"合法"必须引用真实存在的证据编号**。引用不存在的编号 = 该条判定作废
   （`_filter_verdicts` 里的确定性过滤）。反过来，判"编造"**不需要**证据 ——
   它的定义就是"证据里推不出来"。这个不对称是刻意的。
2. **不给它工具**。它结构上什么都做不了，只能裁决：没有工具、没有 side effect、
   不写任何东西。比在提示词里写"不要乱动"强一个数量级。
3. **它永远不阻断**。模型不可用 / 输出不是 JSON / 幻觉出一堆编号 —— 一律退化成
   `status=unreviewed`，主流程照常返回。审查是增强，不是门禁。

<h3>它是旁路里的旁路</h3>
审计已经不改 replies 了，critic 更不会：它只往结果里多挂一个字段，
供日志、评测与将来"要不要重试"的判断使用。
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import llm_service

logger = logging.getLogger(__name__)

STATUS_OK = "ok"
STATUS_UNREVIEWED = "unreviewed"

LABEL_LEGITIMATE = "legitimate_derivation"
LABEL_UNSUPPORTED = "unsupported"
LABEL_UNCLEAR = "unclear"
LABELS = (LABEL_LEGITIMATE, LABEL_UNSUPPORTED, LABEL_UNCLEAR)

# 证据块的大小上限：裁决只需要"能被推出那个数的那些字段"，
# 把整段新闻正文塞进去既贵又会让它开始"总结新闻"（那不是它的工作）。
MAX_EVIDENCE_CHARS = 4000
MAX_CANDIDATES = 8
REVIEW_TEMPERATURE = 0.0
REVIEW_MAX_TOKENS = 900

ADJUDICATION_SYSTEM_PROMPT = """你是**裁决者**，不是作者。有人给你一批"找不到字面依据"的数字，\
你要逐个判断它是**推出来的**还是**编的**。

你会看到三类内容：
1. `candidates`：被标记的数字，每个带 context（它在回复里的上下文片段）。
2. `evidence`：作者当时**实际看到**的工具结果原文，每段带编号 E1、E2…
3. `user_request`：用户原话（用户自己说过的数字不算编造）。

对每个候选数字给出一个判定，label 只能是这三个：
- legitimate_derivation：它确实能从 evidence 推出。必须写 derivation（怎么推的：\
单位换算 / 差值 / 求和 / 比例 / 四舍五入 / 符号由文字承担 …）并给出 evidence_refs。
- unsupported：从 evidence 里推不出来 —— 编造、记错、或把别的标的/指标的数字挪了过来。
- unclear：证据不足以判断（例如证据被裁剪、缺关键字段）。

规则（必须遵守）：
- 判 legitimate_derivation **必须**带 evidence_refs，且编号必须真实存在于 evidence 里。\
引用不存在的编号 = 这条判定无效。
- **不要因为"这个数字看起来很合理"就判它合法**。合理不是依据，能从证据推出来才是。
- **不要使用你自己的市场知识或记忆**：你只能看这里给出的证据。凭印象补充的事实一律不许。
- 判 unsupported 时要说明"证据里有什么、以及为什么推不出它"。
- 只输出 JSON，不要解释、不要代码块以外的任何文字。

输出格式：
{
  "verdicts": [
    {"number": "239.8", "label": "legitimate_derivation",
     "derivation": "证据 E2 的成交额是 2398035（万元），换算成亿元即 239.8035，取一位小数得 239.8",
     "evidence_refs": ["E2"], "confidence": 0.9}
  ],
  "summary": "一句话：这批候选里几条是推导、几条是编造"
}"""


def _evidence_blocks(tool_contents) -> tuple[list[dict], dict]:
    """把"模型看到过的工具结果"编成 E1、E2…，返回 (编号列表, 编号 → 原文)。"""
    blocks = []
    for item in tool_contents or ():
        if isinstance(item, dict):
            text = str(item.get("content") or "")
            tool = str(item.get("tool") or "")
        elif isinstance(item, str):
            text, tool = item, ""
        else:
            continue
        if not text.strip():
            continue
        blocks.append({"tool": tool, "text": text[:MAX_EVIDENCE_CHARS]})
    refs = {f"E{index + 1}": block for index, block in enumerate(blocks)}
    return blocks, refs


def _extract_json(text) -> Optional[dict]:
    """从模型输出里取 JSON 对象（与 consolidate 同一套容错风格）。"""
    if not isinstance(text, str):
        return None
    import re

    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, TypeError, ValueError):
        # 有些模型会把 JSON 前后各加一句话：退一步只取最外层大括号
        start, end = candidate.find("{"), candidate.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(candidate[start:end + 1])
        except (json.JSONDecodeError, TypeError, ValueError):
            return None
    return parsed if isinstance(parsed, dict) else None


def _filter_verdicts(raw_verdicts, candidates: list, known_refs: set) -> tuple[list, list]:
    """确定性过滤：编号对不上、数字不在候选里、label 不认识的一律丢掉。

    这是整套设计里最关键的一步 —— **判"合法"必须引用真实存在的证据编号**。
    少了它，critic 只要顺着说"这是单位换算"就能把编造洗白，而它自己不会被检查。
    """
    kept, dropped = [], []
    wanted = {str(item.get("number")) for item in candidates or ()}
    for item in raw_verdicts or ():
        if not isinstance(item, dict):
            dropped.append({"reason": "not_an_object"})
            continue
        number = str(item.get("number") or "").strip()
        label = str(item.get("label") or "").strip().lower()
        refs = [str(ref).strip() for ref in (item.get("evidence_refs") or [])]

        if number not in wanted:
            dropped.append({"reason": "number_not_a_candidate", "number": number})
            continue
        if label not in LABELS:
            dropped.append({"reason": "unknown_label", "number": number, "label": label})
            continue
        unknown = [ref for ref in refs if ref not in known_refs]
        if unknown:
            dropped.append({"reason": "unknown_evidence_ref", "number": number,
                            "refs": unknown})
            continue
        if label == LABEL_LEGITIMATE and not refs:
            dropped.append({"reason": "legitimate_without_evidence", "number": number})
            continue
        kept.append({
            "number": number,
            "label": label,
            "derivation": str(item.get("derivation") or "").strip(),
            "evidence_refs": refs,
            "confidence": item.get("confidence"),
        })
    return kept, dropped


def adjudicate_claims(candidates, tool_contents, *, user_message: str = "",
                      completion=None) -> dict:
    """裁决一批"无据数字"候选。

    Args:
        candidates: `tool_audit` 给出的候选（`{number, value, context}`）
        tool_contents: 作者当时看到的工具结果（`{tool, content}` 或字符串）
        user_message: 用户原话（用户自己说过的数字不算编造）
        completion: 便于测试注入的 LLM 调用；缺省走 `llm_service.chat_completion`

    永不抛异常：任何异常都退化成 `status=unreviewed`，主流程不受影响。
    """
    try:
        picked = [item for item in (candidates or []) if isinstance(item, dict)][:MAX_CANDIDATES]
        if not picked:
            return {"status": STATUS_OK, "verdicts": [], "dropped": [],
                    "summary": "没有需要裁决的数字", "cost": {"llm_calls": 0}}

        blocks, refs = _evidence_blocks(tool_contents)
        evidence = [{"ref": ref, "tool": block["tool"], "text": block["text"]}
                    for ref, block in refs.items()]
        payload = {
            "user_request": user_message or "",
            "candidates": [{"number": item.get("number"), "context": item.get("context")}
                           for item in picked],
            "evidence": evidence,
        }
        messages = [
            {"role": "system", "content": ADJUDICATION_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False) + "\n\n只输出 JSON。"},
        ]
        call = completion or llm_service.chat_completion
        choice = call(messages, temperature=REVIEW_TEMPERATURE, max_tokens=REVIEW_MAX_TOKENS)
        message = (choice or {}).get("message") if isinstance(choice, dict) else None
        content = (message or {}).get("content") if isinstance(message, dict) else None
        parsed = _extract_json(content)
        if parsed is None:
            logger.info("裁决输出不是合法 JSON，按未审查处理: %s", str(content)[:200])
            return {"status": STATUS_UNREVIEWED, "reason": "invalid_json",
                    "verdicts": [], "dropped": [], "cost": {"llm_calls": 1}}

        kept, dropped = _filter_verdicts(parsed.get("verdicts"), picked, set(refs))
        by_label = {label: [item["number"] for item in kept if item["label"] == label]
                    for label in LABELS}
        return {
            "status": STATUS_OK,
            "verdicts": kept,
            "dropped": dropped,
            "legitimate": by_label[LABEL_LEGITIMATE],
            "unsupported": by_label[LABEL_UNSUPPORTED],
            "unclear": by_label[LABEL_UNCLEAR],
            "summary": str(parsed.get("summary") or "").strip(),
            "evidence_refs": {ref: block["tool"] for ref, block in refs.items()},
            "cost": {"llm_calls": 1},
        }
    except Exception as exc:  # noqa: BLE001 —— 审查是旁路，绝不影响一轮对话
        logger.warning("裁决失败: %s", exc)
        return {"status": STATUS_UNREVIEWED, "reason": str(exc),
                "verdicts": [], "dropped": [], "cost": {"llm_calls": 1}}


def review_turn(audit, tool_contents, *, user_message: str = "", completion=None) -> dict:
    """一轮结束后的入口：有候选才裁决，没有就什么都不做（零成本）。

    为什么以 `audit` 为输入而不是原始 replies：候选已经由确定性审计筛过一遍，
    裁决只处理那一小部分 —— 这正是"确定性过滤网 + LLM 只判边缘情况"的分工。
    """
    audit = audit if isinstance(audit, dict) else {}
    candidates = audit.get("unsupported_claims") or []
    if not candidates:
        return {"status": STATUS_OK, "verdicts": [], "dropped": [],
                "summary": "审计没有报出候选数字", "cost": {"llm_calls": 0}}
    return adjudicate_claims(candidates, tool_contents, user_message=user_message,
                             completion=completion)


def describe() -> dict:
    """给 /health 与调试用：这个裁决者能做什么、绝对不能做什么。"""
    return {
        "statuses": [STATUS_OK, STATUS_UNREVIEWED],
        "labels": list(LABELS),
        "tools": [],
        "blocking": False,
        "requires_evidence_for": LABEL_LEGITIMATE,
        "max_candidates": MAX_CANDIDATES,
        "temperature": REVIEW_TEMPERATURE,
    }
