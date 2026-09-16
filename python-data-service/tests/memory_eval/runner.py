"""跑 golden set，输出指标表与 JSON 报告。

    python -m tests.memory_eval.runner              # 离线确定性跑批（免费，无网络）
    python -m tests.memory_eval.runner --json out.json
    MEMORY_EVAL_LIVE=1 python -m tests.memory_eval.runner --live

退出码：0 = 门禁全过；1 = 有用例失败或指标未达标（可直接用在 CI 里）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# 允许 `python -m tests.memory_eval.runner` 直接跑（把服务根目录加进 sys.path）
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tests.memory_eval import harness  # noqa: E402

DEFAULT_REPORT = Path(__file__).parent / "report.json"


def _print_table(results) -> None:
    print(f"{'用例':<38} {'结果':<6} {'注入字符':>8} {'耗时ms':>8}  说明")
    print("-" * 92)
    for result in results:
        detail = ""
        if result.include_misses:
            detail = "缺：" + "、".join(result.include_misses)
        if result.exclude_violations:
            detail += (" / " if detail else "") + "不该出现：" + "、".join(result.exclude_violations)
        if result.exclude_memory_violations:
            detail += (" / " if detail else "") + "不该进记忆块：" + "、".join(result.exclude_memory_violations)
        if result.count_violations:
            detail += (" / " if detail else "") + "；".join(result.count_violations)
        if result.order_violations:
            detail += (" / " if detail else "") + "；".join(result.order_violations)
        print(f"{result.id:<38} {'PASS' if result.passed else 'FAIL':<6} "
              f"{result.injection_chars:>8} {result.elapsed_ms:>8.1f}  {detail}")


def _print_metrics(metrics: dict, gate_failures: list) -> None:
    print("\n指标")
    print("-" * 92)
    for key, value in metrics.items():
        gate = harness.GATES.get(key)
        mark = ""
        if gate:
            operator, threshold = gate
            ok = (value >= threshold if operator == ">=" else
                  value <= threshold if operator == "<=" else value == threshold)
            mark = f"  [{ '达标' if ok else '未达标' } {operator} {threshold}]"
        print(f"  {key:<24} {value}{mark}")
    if gate_failures:
        print("\n门禁未通过：")
        for failure in gate_failures:
            print(f"  ✗ {failure}")
    else:
        print("\n门禁全部通过。")


# ==================== live 模式：真调 LLM 评抽取质量 ====================

def _normalize_object(value) -> str:
    text = str(value or "").strip().replace(" ", "").replace("%", "").replace("约", "")
    return text


def _object_matches(expected, actual) -> bool:
    """值比较要宽容一点：'5' / '5%' / '-5' 在语义上是同一个止损幅度。

    严苛到字面相等会让评测天天因为"模型多写了个百分号"而红，
    这种红没有信息量，反而会让人不再看报告。
    """
    left, right = _normalize_object(expected), _normalize_object(actual)
    if left == right:
        return True
    try:
        return abs(float(left)) == abs(float(right))
    except (TypeError, ValueError):
        return left in right or right in left


def _fact_matches(expected: dict, actual: dict) -> bool:
    from agent import facts as facts_mod

    expected_predicate = facts_mod.canonical_predicate(expected.get("predicate"))
    actual_predicate = facts_mod.canonical_predicate(actual.get("predicate"))
    if expected_predicate != actual_predicate:
        return False
    expected_subject = str(expected.get("subject") or "").strip().lower()
    actual_subject = str(actual.get("subject") or "").strip().lower()
    if expected_subject and expected_subject != actual_subject:
        return False
    if "object" in expected and not _object_matches(expected["object"], actual.get("object")):
        return False
    return True


def run_live_cases(verbose: bool = True) -> dict:
    """真调 LLM 跑抽取，算 precision / recall。

    这是 golden set 里唯一花钱、且不稳定的部分，所以默认不跑（`--live` 或 `MEMORY_EVAL_LIVE=1`）。
    但它值得存在：prompt 改坏了最常见、最难察觉的表现就是"不再抽事实了"，
    而离线用例喂的是已经抽好的数据，测不出这个。
    """
    import pytest

    from agent import consolidate, memory_store, vector_index
    from tests.memory_eval.harness import load_cases

    live_cases = load_cases()["live_cases"]
    rows = []
    hit_total = expected_total = extracted_total = 0

    for case in live_cases:
        monkeypatch = pytest.MonkeyPatch()
        captured = {"facts": [], "episode": None}
        try:
            monkeypatch.setattr(memory_store, "list_events",
                                lambda user_id, session_key, events=case.get("ledger"): events)
            monkeypatch.setattr(memory_store, "save_episode",
                                lambda **kwargs: captured.update(episode=kwargs) or {"version": 1})
            monkeypatch.setattr(memory_store, "save_facts",
                                lambda user_id, session_key, facts, source_event_ids=None:
                                captured.update(facts=facts) or {"results": []})
            monkeypatch.setattr(memory_store, "save_lessons",
                                lambda *args, **kwargs: {"results": []})
            monkeypatch.setattr(vector_index, "invalidate", lambda user_id=None: None)

            consolidate.consolidate_session(1, case.get("session_key"))
        finally:
            monkeypatch.undo()

        extracted = captured["facts"] or []
        expectation = case.get("expect_extraction") or {}
        wanted = expectation.get("facts") or []
        any_of = expectation.get("any_of") or []

        hits = [want for want in wanted if any(_fact_matches(want, got) for got in extracted)]
        # any_of：只要抽到其中一个就算命中（用于"模型表达方式不唯一"的期望）
        any_hit = any(any(_fact_matches(want, got) for want in any_of) for got in extracted) if any_of else None

        hit_total += len(hits) + (1 if any_hit else 0)
        expected_total += len(wanted) + (1 if any_of else 0)
        extracted_total += len(extracted)

        rows.append({
            "id": case.get("id"),
            "ok": len(hits) == len(wanted) and (any_hit is not False),
            "expected": wanted + any_of,
            "extracted": [{"subject": f.get("subject"), "predicate": f.get("predicate"),
                           "object": f.get("object")} for f in extracted],
        })
        if verbose:
            print(f"  {case.get('id'):<32} {'OK' if rows[-1]['ok'] else 'MISS'}  "
                  f"抽出 {len(extracted)} 条，命中 {len(hits)}/{len(wanted)}")

    return {
        "live_cases": len(live_cases),
        "live_recall": round(hit_total / expected_total, 4) if expected_total else 1.0,
        "extracted_total": extracted_total,
        "rows": rows,
    }


def _write_report(path: Path, report: dict, quiet: bool) -> Path:
    """写报告。写不进去也不能让跑批本身失败 —— 指标已经算完并打印了。

    （真实场景遇到过：受限环境里工作区目录对子进程只读，写文件抛 PermissionError，
    结果一个全绿的跑批以一个吓人的 traceback 收尾。指标工具不该这样收尾。）
    """
    import tempfile

    try:
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError as exc:
        fallback = Path(tempfile.gettempdir()) / "memory-eval-report.json"
        try:
            fallback.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError:
            if not quiet:
                print(f"\n报告写入失败（{exc}），且临时目录也不可写；指标见上方输出。")
            return path
        if not quiet:
            print(f"\n{path} 不可写（{exc}），报告已改写到 {fallback}")
        return fallback


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="记忆质量 golden set 跑批")
    parser.add_argument("--json", type=Path, default=DEFAULT_REPORT, help="报告输出路径")
    parser.add_argument("--live", action="store_true",
                        help="额外跑需要真实 LLM 的抽取评测（花钱、结果不稳定）")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    import pytest

    from tests.memory_eval.harness import run_case

    cases = harness.load_cases()["cases"]
    results = []
    for case in cases:
        monkeypatch = pytest.MonkeyPatch()
        try:
            results.append(run_case(case, monkeypatch))
        finally:
            monkeypatch.undo()

    metrics = harness.summarize(results)
    gate_failures = harness.check_gates(metrics)

    if not args.quiet:
        _print_table(results)
        _print_metrics(metrics, gate_failures)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "metrics": metrics,
        "gate_failures": gate_failures,
        "cases": [
            {"id": r.id, "title": r.title, "passed": r.passed,
             "missing": r.include_misses, "unexpected": r.exclude_violations,
             "unexpected_in_memory": r.exclude_memory_violations,
             "count_violations": r.count_violations, "order_violations": r.order_violations,
             "injection_chars": r.injection_chars, "elapsed_ms": round(r.elapsed_ms, 2)}
            for r in results
        ],
    }

    if args.live or os.getenv("MEMORY_EVAL_LIVE") == "1":
        if not args.quiet:
            print("\nlive 抽取评测（真实 LLM）")
            print("-" * 92)
        report["live"] = run_live_cases(verbose=not args.quiet)
        if not args.quiet:
            print(f"  抽取召回率 {report['live']['live_recall']}")

    written = _write_report(args.json, report, args.quiet)
    if not args.quiet:
        print(f"\n报告已写入 {written}")

    return 1 if (gate_failures or metrics["failed"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
