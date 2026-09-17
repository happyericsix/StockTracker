# -*- coding: utf-8 -*-
"""裁决者（critic）的真调评测：**它到底判不判得对**。

<h3>两层</h3>
1. **离线（默认跑，零成本）**：用例本身是否"诚实" —— 判"合法推导"的用例，证据里必须
   真的含有推导所需的原始数字（例如 239.8 亿元那条，证据里必须有 2398035）。
   没有这一层，golden set 会慢慢漂成"我记忆里的样子"，而它本来是唯一的判据。
2. **在线（`REVIEW_EVAL_LIVE=1` 才跑，4 次 LLM 调用）**：真调模型逐个裁决，算准确率。

<h3>门禁为什么这样定</h3>
- 四条全对当然好，但真正**不能错**的是两条：**不能把合法推导判成编造**（冤枉），
  **不能把编造判成合法**（放过）。前者会让用户失去对助手的信任，后者会让他们按假数字做决定。
  所以门禁是：合法用例必须 >= 2 条判对，编造用例必须 2 条全判对。
"""
import os
import re
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import review  # noqa: E402
from agent import strategy_review as sr  # noqa: E402

CASES_PATH = Path(__file__).resolve().parent / "review_eval" / "cases.yaml"
STRATEGY_CASES_PATH = Path(__file__).resolve().parent / "review_eval" / "strategy_cases.yaml"
LIVE = os.getenv("REVIEW_EVAL_LIVE") == "1"

CASES = (yaml.safe_load(CASES_PATH.read_text(encoding="utf-8")) or {}).get("cases") or []
STRATEGY_CASES = ((yaml.safe_load(STRATEGY_CASES_PATH.read_text(encoding="utf-8")) or {})
                  .get("cases") or [])


# ==================== 1. 离线：用例必须诚实 ====================


def test_cases_are_loadable_and_complete():
    assert len(CASES) >= 4, CASES_PATH
    for case in CASES:
        assert case.get("why"), f"{case['id']} 缺少 why"
        assert case.get("source") in ("real", "real_partial", "synthetic"), case["id"]
        assert case["candidates"], case["id"]
        assert case["evidence"], case["id"]
        assert case["expect_label"] in review.LABELS, case["id"]
        # 一条用例只裁决一个数字：多了会让"判对没判对"变得说不清
        assert len(case["candidates"]) == 1, case["id"]


def test_legitimate_cases_have_the_raw_number_in_evidence():
    """判"合法推导"的用例，证据里必须真的含推导所需的原始数字。

    否则它测的不是"模型会不会推导"，而是"模型会不会配合我编"。
    """
    required = {
        "sign_by_word": "-10.88",       # 涨跌额（符号由文字承担）
        "unit_conversion": "2398035",   # 成交额（万元）
    }
    for case in CASES:
        if case["expect_label"] != review.LABEL_LEGITIMATE:
            continue
        needle = required.get(case["id"])
        assert needle, f"{case['id']} 没有登记它依赖的原始数字"
        haystack = " ".join(item["content"] for item in case["evidence"])
        assert needle in haystack, f"{case['id']} 的证据里找不到 {needle}"


def test_unsupported_cases_lack_the_number_in_evidence():
    """反向：判"编造"的用例，证据里不能出现那个数字（否则它就不是编造）。"""
    for case in CASES:
        if case["expect_label"] != review.LABEL_UNSUPPORTED:
            continue
        number = case["candidates"][0]["number"]
        haystack = " ".join(item["content"] for item in case["evidence"])
        assert number not in haystack, f"{case['id']} 的证据里出现了 {number}"


def test_context_contains_the_candidate():
    """候选数字必须真的出现在它的上下文片段里（片段是给人 triage 用的，不能对不上）。"""
    for case in CASES:
        number = case["candidates"][0]["number"]
        assert number in case["candidates"][0]["context"], case["id"]


# ==================== 2. 在线：真调模型 ====================


def _judge(case):
    result = review.adjudicate_claims(case["candidates"], case["evidence"],
                                      user_message=case.get("user_request", ""))
    number = case["candidates"][0]["number"]
    labels = {item["number"]: item["label"] for item in result.get("verdicts") or []}
    return result, labels.get(number)


@pytest.mark.skipif(not LIVE, reason="需要真实 LLM：设 REVIEW_EVAL_LIVE=1 才跑")
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_critic_judges_each_real_sample(case):
    result, label = _judge(case)
    assert result["status"] == "ok", result
    assert label == case["expect_label"], (
        f"{case['id']}：期望 {case['expect_label']}，实际 {label}"
        f"（verdicts={result['verdicts']}，dropped={result['dropped']}）")


@pytest.mark.skipif(not LIVE, reason="需要真实 LLM：设 REVIEW_EVAL_LIVE=1 才跑")
def test_critic_never_accuses_a_legitimate_derivation_and_never_clears_a_fabrication():
    """门禁：**冤枉**与**放过**这两类错分别设上限，而不是只看总准确率。"""
    legitimate_cases = [c for c in CASES if c["expect_label"] == review.LABEL_LEGITIMATE]
    unsupported_cases = [c for c in CASES if c["expect_label"] == review.LABEL_UNSUPPORTED]

    legitimate_ok = sum(1 for case in legitimate_cases if _judge(case)[1] == review.LABEL_LEGITIMATE)
    unsupported_ok = sum(1 for case in unsupported_cases
                         if _judge(case)[1] == review.LABEL_UNSUPPORTED)

    failures = []
    if legitimate_ok < len(legitimate_cases):
        failures.append(f"把 {len(legitimate_cases) - legitimate_ok} 条合法推导判成了别的东西（冤枉）")
    if unsupported_ok < len(unsupported_cases):
        failures.append(f"放过了 {len(unsupported_cases) - unsupported_ok} 条编造（漏报）")
    assert not failures, "；".join(failures)


# ==================== 3. 策略审查（需要模型的那一半） ====================


def _value_at(config, path):
    """按 JSON 路径取值（与 strategy_review._path_exists 同一套路径语法）。"""
    current = config
    for token in path.replace("[", ".[").split("."):
        if not token:
            continue
        match = re.fullmatch(r"(\w+)?\[(\d+)\]", token)
        if match:
            if match.group(1):
                current = current[match.group(1)]
            current = current[int(match.group(2))]
        else:
            current = current[token]
    return current


def test_strategy_cases_are_honest():
    """用例必须真的坏在它声称的地方 —— 否则测的是"模型会不会配合我"。"""
    assert len(STRATEGY_CASES) >= 3, STRATEGY_CASES_PATH
    for case in STRATEGY_CASES:
        assert case.get("why"), case["id"]
        assert case.get("user_request") and case.get("strategy"), case["id"]
        structural = sr.structural_findings(case["strategy"])
        if case["expect_verdict"] == "accept":
            # 期望通过：结构检查必须干净，否则"没误报"可能是靠一条免费发现换来的
            assert structural == [], f"{case['id']} 带着结构问题：{structural}"
            continue
        # 期望不通过：必须声明坏在哪，而且那处坏要能程序化验证
        assert ("expect_number" in case or "expect_absent_condition" in case
                or case.get("expect_dsl_gap")), case["id"]
        if case.get("expect_dsl_gap"):
            # 声称"这句要求没有等价写法"：那就确认 DSL 里**确实**没有那类条件。
            # 哪天有人补了（例如 price_below_ma），这里会失败，提醒重新审视这条用例。
            from agent.strategy_schema import CONDITION_TYPES

            assert not any(item.startswith("price") and "ma" in item
                           for item in CONDITION_TYPES), \
                f"{case['id']} 声称是 DSL 缺口，但 DSL 已经有等价条件了"
        if "expect_number" in case:
            actual = _value_at(case["strategy"], case["actual_value_path"])
            assert abs(abs(float(actual)) - abs(float(case["expect_number"]))) > 1e-9, \
                f"{case['id']} 声称值对不上，实际却是 {actual}"
        if "expect_absent_condition" in case:
            types = [item.get("type") for item in case["strategy"]["exit"]["conditions"]]
            assert case["expect_absent_condition"] not in types, case["id"]


@pytest.mark.skipif(not LIVE, reason="需要真实 LLM：设 REVIEW_EVAL_LIVE=1 才跑")
@pytest.mark.parametrize("case", STRATEGY_CASES, ids=[c["id"] for c in STRATEGY_CASES])
def test_critic_reviews_strategies(case):
    result = sr.review_strategy(case["user_request"], case["strategy"])
    assert result["status"] == sr.STATUS_OK, result

    if case["expect_verdict"] == "accept":
        assert result["verdict"] == sr.VERDICT_ACCEPT, (
            f"{case['id']}：本该通过却判成 {result['verdict']}"
            f"（findings={result['findings']}）")
        return

    assert result["verdict"] != sr.VERDICT_ACCEPT, f"{case['id']}：该报的问题没报出来"
    assert any(item["kind"] == "requirement_mismatch" for item in result["findings"]), \
        f"{case['id']}：判决不对，但没有一条需求不一致的判定：{result['findings']}"


@pytest.mark.skipif(not LIVE, reason="需要真实 LLM：设 REVIEW_EVAL_LIVE=1 才跑")
def test_strategy_review_neither_misses_nor_false_alarms():
    """门禁：该过的必须过（不误报），该拦的必须拦（不放过）。"""
    failures = []
    for case in STRATEGY_CASES:
        result = sr.review_strategy(case["user_request"], case["strategy"])
        verdict = result.get("verdict")
        if case["expect_verdict"] == "accept" and verdict != sr.VERDICT_ACCEPT:
            failures.append(f"{case['id']}：误报（判成 {verdict}）")
        if case["expect_verdict"] != "accept" and verdict == sr.VERDICT_ACCEPT:
            failures.append(f"{case['id']}：漏报（判成 accept）")
    assert not failures, "；".join(failures)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
