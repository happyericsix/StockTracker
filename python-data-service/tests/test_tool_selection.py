# -*- coding: utf-8 -*-
"""工具选择与参数质量（T1，真调 LLM）。

默认跳过：这是唯一必须花真钱、且结果有波动的评测 —— 因为"选哪个工具"是模型的判断，
用假 LLM 测它等于测自己写的替身。

    TOOL_EVAL_LIVE=1 pytest tests/test_tool_selection.py -q
    python -m tests.tool_eval.runner            # 带 A/B（加示例 vs 不加示例）
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.tool_eval import runner  # noqa: E402

LIVE = os.getenv("TOOL_EVAL_LIVE") == "1"
CASES = runner.load_cases()


@pytest.mark.skipif(not LIVE, reason="需要真实 LLM：设 TOOL_EVAL_LIVE=1 才跑")
@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_tool_choice_is_reasonable(case):
    row = runner.run_case(case, include_examples=True)

    if case.get("expect_no_tool"):
        assert not row["picked"], f"寒暄不该调工具，实际调了 {row['picked']}"
    else:
        assert any(name in case["expect_tools"] for name in row["picked"]) or not row["picked"], \
            f"选错了工具：{row['picked']}（期望其中之一 {case['expect_tools']}）"
    assert not row["args_problems"], f"参数不合法：{row['args_problems']}"


@pytest.mark.skipif(not LIVE, reason="需要真实 LLM：设 TOOL_EVAL_LIVE=1 才跑")
def test_gates_hold_on_the_whole_set():
    rows = [runner.run_case(case, include_examples=True) for case in CASES]
    metrics = runner.summarize(rows)

    assert not runner.check_gates(metrics), f"门禁未通过：{metrics}"
