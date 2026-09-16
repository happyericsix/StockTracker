# -*- coding: utf-8 -*-
"""记忆质量 golden set（pytest 入口）。

每个用例断言"模型最终看到的内容"，这就是记忆质量在工程上的落点。
用例本体在 `memory_eval/cases.yaml`，加场景不需要改这个文件。

跑法：
    pytest tests/test_memory_golden.py -v                 # 离线、确定性、免费
    python -m tests.memory_eval.runner                    # 加指标表与 JSON 报告
    MEMORY_EVAL_LIVE=1 pytest tests/test_memory_golden.py -k live   # 真调 LLM 评抽取

live 模式默认跳过：它花钱、结果有波动，但它是唯一能发现
"prompt 改坏了导致不再抽事实"的检查 —— 离线用例喂的是已经抽好的数据。
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.memory_eval import harness  # noqa: E402

CASES = harness.load_cases()["cases"]
LIVE = os.getenv("MEMORY_EVAL_LIVE") == "1"


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_golden_case(case, monkeypatch):
    result = harness.run_case(case, monkeypatch)

    assert not result.include_misses, f"该出现的记忆没注入：{result.include_misses}"
    assert not result.exclude_violations, f"不该出现的记忆被注入了：{result.exclude_violations}"
    assert not result.exclude_memory_violations, \
        f"不该进记忆块的内容进去了：{result.exclude_memory_violations}"
    assert not result.count_violations, result.count_violations
    assert not result.order_violations, result.order_violations


def test_全量指标达标(monkeypatch):
    """门禁：这些数字不许退化。阈值定义在 harness.GATES。"""
    results = [harness.run_case(case, monkeypatch) for case in CASES]
    metrics = harness.summarize(results)
    failures = harness.check_gates(metrics)

    assert not failures, f"记忆质量门禁未通过：{failures}；指标 = {metrics}"


def test_每个注入块都带安全声明与时间锚点(monkeypatch):
    """记忆是**不可信内容**：没有"这是数据不是指令"的声明就不该注入。

    这条单独测（而不是只看聚合比例），因为它是防记忆投毒的第一道闸门，
    漏一个用例就等于给模型留了一个"把记忆当指令执行"的口子。
    """
    for case in CASES:
        result = harness.run_case(case, monkeypatch)
        if not result.memory_block:
            continue
        assert result.has_safety_notice, f"{case['id']} 的注入块缺少安全声明"
        assert result.has_time_anchor, f"{case['id']} 的注入块缺少今天的时间锚点"


@pytest.mark.skipif(not LIVE, reason="需要真实 LLM：设 MEMORY_EVAL_LIVE=1 才跑")
def test_live_抽取质量():
    """真调 LLM 跑巩固，检查该抽出的事实有没有抽出来。

    这是唯一能发现"prompt 改坏了不再抽事实"的检查：离线用例喂的是已抽好的数据。
    """
    from tests.memory_eval.runner import run_live_cases

    report = run_live_cases(verbose=True)

    assert report["live_cases"] > 0, "没有 live 用例"
    # 阈值刻意留松：模型输出有波动，这里只拦"整类失效"，不惩罚单次措辞差异
    assert report["live_recall"] >= 0.5, f"抽取召回率过低：{report}"
