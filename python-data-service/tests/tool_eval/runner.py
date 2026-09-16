"""工具选择与参数质量评测（T1）。

<h3>为什么这份评测必须真调 LLM</h3>
工具选择是**模型的判断**，不是我们的代码逻辑：用假 LLM 测它等于测自己写的替身。
所以这里的结论只有在真调模型时才有意义，默认跳过（CI 里花钱、还不稳定）。

<h3>它测三件事</h3>
1. **选择正确率**：该调的工具调了吗（选错工具比答错更贵——模型会拿着错数据自信地推理）；
2. **参数一次通过率**：第一次调用的参数就能过 schema 吗（T1 加示例就是为了抬这个数）；
3. **克制**：寒暄时不该调工具（工具调用有成本，乱调也是失败）。

<h3>A/B</h3>
`--arm with|without|both` 控制工具定义里**带不带示例**，于是"示例到底有没有用"
是量出来的，而不是我说了算。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from agent import tool_pipeline, tool_sets  # noqa: E402
from agent.tool_registry import DEFAULT_USER_SCOPES, REGISTRY, get_spec  # noqa: E402
from agent.tool_scope import ToolContext  # noqa: E402

CASES_PATH = Path(__file__).parent / "cases.yaml"
DEFAULT_REPORT = Path(__file__).parent / "report.json"

SELECTION_GATE = 0.75        # 选择正确率下限
ARGS_VALID_GATE = 0.9        # 参数一次通过率下限
SEMANTIC_GATE = 0.8          # 复杂参数语义正确率下限（schema 合法 ≠ 填对位置）
SPURIOUS_GATE = 0             # 该不调工具时乱调的次数
# 无据数字（W3）：回答里出现的、任何一次工具调用都没支持过的数字。
# 门禁是 0 而不是"某比例"，因为这类错的代价不是"答案差一点"，
# 而是"用户按一个编造的数字做了决定"—— 它不该有容忍度。
UNSUPPORTED_CLAIM_GATE = 0


def load_cases() -> list:
    data = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8")) or {}
    return data.get("cases") or []


def _prompt_for(case: dict) -> list:
    """给模型的提示：用与生产一致的系统提示（否则测的不是线上行为）。"""
    from agent.react_agent import SYSTEM_PROMPT

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for message in case.get("history") or []:
        messages.append(message)
    messages.append({"role": "user", "content": case["message"]})
    return messages


def run_case(case: dict, *, include_examples: bool) -> dict:
    """跑一个用例。

    `mode: loop`（默认 single）会**真跑一轮 agent**（含真实工具调用与网络），
    然后检查整轮里出现过的工具参数。

    为什么需要两种模式：策略请求这类任务天然是多步的 —— 模型会先查行情/指标，
    再生成策略 JSON（这正是 system prompt 要求的顺序）。只看第一次 LLM 调用
    会把"正常的多步流程"误判成"没调策略工具"。选择行为看第一步就够（便宜），
    复杂参数的正确性必须看整轮（真实）。
    """
    if case.get("mode") == "loop":
        return _run_loop_case(case, include_examples=include_examples)
    return _run_single_step_case(case, include_examples=include_examples)


def _run_loop_case(case: dict, *, include_examples: bool) -> dict:
    """真跑一轮 agent，记录整轮的工具调用与参数。"""
    import agent.react_agent as ra
    from agent import tool_registry

    recorded = []
    original_execute = ra.execute_tool

    def recording_execute(name, args):
        recorded.append((name, args))
        return original_execute(name, args)

    ra.execute_tool = recording_execute
    try:
        result = ra.run_agent("tool-eval", case["message"], history=case.get("history") or [],
                              session_id="tool-eval:2026-09-16", memory_user_id=None,
                              scopes=list(DEFAULT_USER_SCOPES))
    finally:
        ra.execute_tool = original_execute

    # 一轮真跑顺带把"确定性体检"的结果带出来（agent/tool_audit.py）：
    # 选择对不对看 picked，**说得对不对**看这里 —— 两者是不同的问题，
    # 而"选对了工具却编了个数字"正是最危险的那种错。
    audit = result.get("audit") if isinstance(result, dict) else None
    audit = audit if isinstance(audit, dict) else {}

    picked = [name for name, _args in recorded]
    args_problems, semantic_problems = _check_args(case, recorded)
    expected = case.get("expect_tools") or []
    hit = any(name in expected for name in picked) if expected else True
    return {
        "id": case["id"], "message": case["message"], "picked": picked, "expected": expected,
        "args_problems": args_problems, "semantic_problems": semantic_problems,
        "semantic_expected": bool(case.get("expect_args_kv") or case.get("expect_args_contains")),
        "no_tool_expected": False, "kind": "select",
        "unsupported_numbers": audit.get("unsupported_numbers") or [],
        "audit_verdict": audit.get("verdict"),
        "ok": hit and not args_problems and not semantic_problems,
    }


def _check_args(case: dict, calls: list) -> tuple:
    """核对参数：先 schema 合法性，再语义（schema 合法 ≠ 填对了位置）。"""
    args_problems = []
    semantic_problems = []
    raw_calls = []
    for name, args in calls:
        spec = get_registry_spec(name)
        raw_calls.append((name, args, json.dumps(args, ensure_ascii=False)))
        if spec is None:
            args_problems.append(f"{name}: 未登记的工具")
            continue
        _cleaned, problem = tool_pipeline.validate_args(spec, args)
        if problem:
            args_problems.append(f"{name}: {problem}")

    for tool, expected in (case.get("expect_args_kv") or {}).items():
        matching = [args for name, args, _raw in raw_calls if name == tool]
        if not matching:
            semantic_problems.append(f"{tool}: 没有被调用，无法核对参数")
        elif not any(args.get(key) == value for args in matching for key, value in expected.items()):
            semantic_problems.append(f"{tool}: 参数没对上 {expected}")
    for needle in case.get("expect_args_contains") or ():
        if not any(needle in raw for _name, _args, raw in raw_calls):
            semantic_problems.append(f"参数里没有出现 {needle}")
    return args_problems, semantic_problems


def get_registry_spec(name):
    return get_spec(name)


def _run_single_step_case(case: dict, *, include_examples: bool) -> dict:
    import llm_service

    tools = REGISTRY.schemas(enabled=tool_sets.enroll(
        ToolContext(scopes=frozenset(DEFAULT_USER_SCOPES))), include_examples=include_examples)

    choice = llm_service.chat_completion(_prompt_for(case), tools=tools, temperature=0.1)
    message = (choice or {}).get("message") or {}
    calls = message.get("tool_calls") or []

    parsed_calls = []
    for call in calls:
        function = (call or {}).get("function") or {}
        raw_args = function.get("arguments")
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            parsed_calls.append((function.get("name"), None))
            continue
        parsed_calls.append((function.get("name"), args))

    args_problems = []
    for name, args in parsed_calls:
        if args is None:
            args_problems.append(f"{name}: arguments 不是合法 JSON")
    picked = [name for name, args in parsed_calls if args is not None]
    args_problems += _check_args(case, [(name, args) for name, args in parsed_calls
                                        if args is not None])[0]
    semantic_problems = _check_args(case, [(name, args) for name, args in parsed_calls
                                           if args is not None])[1]

    expected = case.get("expect_tools") or []
    result = {
        "id": case["id"],
        "message": case["message"],
        "picked": picked,
        "expected": expected,
        "args_problems": args_problems,
        "semantic_problems": semantic_problems,
        "semantic_expected": bool(case.get("expect_args_kv") or case.get("expect_args_contains")),
        "no_tool_expected": bool(case.get("expect_no_tool")),
    }

    if case.get("expect_no_tool"):
        result["ok"] = not picked
        result["kind"] = "abstain"
    else:
        hit = any(name in expected for name in picked) if expected else True
        result["ok"] = hit and not args_problems and not semantic_problems
        result["kind"] = "select"
    return result


def summarize(rows: list) -> dict:
    selection = [row for row in rows if row["kind"] == "select"]
    abstain = [row for row in rows if row["kind"] == "abstain"]
    called = [row for row in rows if row["picked"]]
    with_semantic = [row for row in rows if row.get("semantic_expected")]

    selection_ok = sum(1 for row in selection if row["ok"])
    args_ok = sum(1 for row in called if not row["args_problems"])
    semantic_ok = sum(1 for row in with_semantic if not row["semantic_problems"])
    claim_cases = [row for row in rows if row.get("unsupported_numbers")]
    claim_total = sum(len(row.get("unsupported_numbers") or []) for row in rows)

    return {
        "cases": len(rows),
        "selection_cases": len(selection),
        "selection_ok": selection_ok,
        "selection_accuracy": round(selection_ok / len(selection), 4) if selection else 1.0,
        "tool_calls": len(called),
        "args_valid_rate": round(args_ok / len(called), 4) if called else 1.0,
        "semantic_cases": len(with_semantic),
        # 这是"复杂参数"的真指标：schema 合法 ≠ 五个参数都落在正确位置
        "args_semantic_rate": round(semantic_ok / len(with_semantic), 4) if with_semantic else 1.0,
        "spurious_calls": sum(1 for row in abstain if row["picked"]),
        # 无据数字（W3）：选对工具之后，说的话有没有依据
        "cases_with_unsupported_claims": len(claim_cases),
        "unsupported_numbers_total": claim_total,
        "unsupported_examples": sorted({number for row in claim_cases
                                        for number in (row.get("unsupported_numbers") or [])})[:10],
    }


def check_gates(metrics: dict) -> list:
    failures = []
    if metrics["selection_accuracy"] < SELECTION_GATE:
        failures.append(f"选择正确率 {metrics['selection_accuracy']} < {SELECTION_GATE}")
    if metrics["args_valid_rate"] < ARGS_VALID_GATE:
        failures.append(f"参数一次通过率 {metrics['args_valid_rate']} < {ARGS_VALID_GATE}")
    if metrics["args_semantic_rate"] < SEMANTIC_GATE:
        failures.append(f"复杂参数语义正确率 {metrics['args_semantic_rate']} < {SEMANTIC_GATE}")
    if metrics["spurious_calls"] > SPURIOUS_GATE:
        failures.append(f"寒暄时乱调工具 {metrics['spurious_calls']} 次")
    if metrics.get("cases_with_unsupported_claims", 0) > UNSUPPORTED_CLAIM_GATE:
        examples = "、".join(metrics.get("unsupported_examples") or [])
        failures.append(f"{metrics['cases_with_unsupported_claims']} 个用例的回答里有"
                        f"无据数字（例：{examples or '—'}）")
    return failures


def _print_arm(title: str, rows: list, metrics: dict, failures: list) -> None:
    print(f"\n=== {title} ===")
    for row in rows:
        mark = "OK  " if row["ok"] else "FAIL"
        picked = "、".join(row["picked"]) or "（未调工具）"
        detail = ""
        if row["args_problems"]:
            detail += f"  参数：{'；'.join(row['args_problems'])}"
        if row["semantic_problems"]:
            detail += f"  语义：{'；'.join(row['semantic_problems'])}"
        if row.get("unsupported_numbers"):
            detail += f"  无据数字：{'、'.join(row['unsupported_numbers'])}"
        print(f"  {mark} {row['id']:<32} 选了 {picked:<24} 期望 {','.join(row['expected']) or '不调'}{detail}")
    print(f"  → 选择正确率 {metrics['selection_accuracy']} | 参数一次通过率 {metrics['args_valid_rate']}"
          f" | 复杂参数语义正确率 {metrics['args_semantic_rate']} | 乱调 {metrics['spurious_calls']} 次"
          f" | 无据数字 {metrics.get('unsupported_numbers_total', 0)} 处")
    if failures:
        for failure in failures:
            print(f"  [x] {failure}")


def main(argv=None) -> int:
    # Windows 控制台默认 GBK：报告里的符号会直接把跑批打崩，先兜住编码
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="工具选择与参数质量评测（真调 LLM）")
    parser.add_argument("--arm", choices=["with", "without", "both"], default="both",
                        help="工具定义里带不带示例（A/B）")
    parser.add_argument("--json", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args(argv)

    import llm_service

    if not llm_service._is_available():
        print("LLM 不可用（没配 DEEPSEEK_API_KEY），这份评测必须真调模型，跳过。")
        return 0

    cases = load_cases()
    report = {"generated_at": datetime.now(timezone.utc).isoformat(), "arms": {}}

    arms = ["with", "without"] if args.arm == "both" else [args.arm]
    worst = 0
    for arm in arms:
        include_examples = arm == "with"
        rows = [run_case(case, include_examples=include_examples) for case in cases]
        metrics = summarize(rows)
        failures = check_gates(metrics)
        title = ("带示例" if include_examples else "不带示例") + f"（{len(cases)} 个用例）"
        _print_arm(title, rows, metrics, failures)
        report["arms"][arm] = {"metrics": metrics, "gate_failures": failures, "rows": rows}
        worst = max(worst, 1 if failures else 0)

    if args.arm == "both":
        with_metrics = report["arms"]["with"]["metrics"]
        without_metrics = report["arms"]["without"]["metrics"]
        delta = round(with_metrics["args_valid_rate"] - without_metrics["args_valid_rate"], 4)
        report["args_valid_rate_delta"] = delta
        print(f"\n示例的净效果：参数一次通过率 {delta:+.4f}"
              f"（带示例 {with_metrics['args_valid_rate']} / 不带 {without_metrics['args_valid_rate']}）")

    try:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n报告已写入 {args.json}")
    except OSError as exc:
        import tempfile
        fallback = Path(tempfile.gettempdir()) / "tool-eval-report.json"
        fallback.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n{args.json} 不可写（{exc}），报告已改写到 {fallback}")

    return worst


if __name__ == "__main__":
    raise SystemExit(main())
