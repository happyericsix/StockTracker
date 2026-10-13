# -*- coding: utf-8 -*-
"""委员会消融的口径用例：变体独立性、judge 子集成本、中止纪律与产物落盘。

FakeLLM 全程 HOLD（RouterLLM 模式）：消融工具的**机制**不需要真模型——
真跑（成本对比）是 CLI 的事，测试只钉住"同一行情下各变体真的只差角色数"。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from finetune import committee_ablation as ca  # noqa: E402


class HoldLLM:
    """所有角色一律 HOLD；记录调用次数与 token。"""

    def __init__(self):
        self.calls = 0
        self.tokens = 0

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        self.calls += 1
        self.tokens += 300
        text = ('FINAL TRANSACTION PROPOSAL: **HOLD**\n'
                '```json\n{"signal":"hold","size_fraction":0.0}\n```')
        return {"message": {"role": "assistant", "content": text},
                "usage": {"prompt_tokens": 300, "completion_tokens": 0,
                          "total_tokens": 300}}


class DeadLLM:
    """永远调不成（模拟 402/断网）：消融必须显式中止，不许跑出假 HOLD 天。"""

    def __init__(self):
        self.calls = 0

    def __call__(self, messages, temperature=0.2, max_tokens=800):
        self.calls += 1
        raise RuntimeError("402 Payment Required")


def make_records(n=120):
    """平缓上行的日线：足够预热（60 根均线）+ 一段回放窗口。"""
    from datetime import date, timedelta
    records = []
    price = 100.0
    d = date(2026, 1, 2)
    for _ in range(n):
        records.append({"date": d.isoformat(), "open": price, "close": price,
                        "high": price + 0.2, "low": price - 0.2, "volume": 1000})
        price += 0.2
        d += timedelta(days=1)
    return records


def run_direct(tmp_path, llm, variants=None):
    """不走 run_ablation（它取网络数据），直接跑 run_variant：测试只关心机制。"""
    records = make_records()
    config = json.loads(json.dumps(ca.DEFAULT_CONFIG))
    config["symbol"] = "601988"
    out = {}
    for name, roles in (variants or ca.VARIANTS).items():
        out[name] = ca.run_variant(name, roles, "601988", records,
                                   "2026-02-01", "2026-03-01", config,
                                   completion=llm)
    return out


def test_judge_subset_costs_one_call_per_convene(tmp_path):
    llm = HoldLLM()
    reports = run_direct(tmp_path, llm)

    judge = reports["judge"]
    full = reports["full8"]
    assert judge["error"] is None and full["error"] is None
    assert judge["convened"] > 0, "平缓上行 + 首开/stale 门控下应当真的开过会"
    # 同一门控下：full8 的调用量 = judge 的 8 倍（成本结构的直接验证）
    assert full["llm_calls"] == judge["llm_calls"] * len(ca.VARIANTS["full8"])
    assert judge["llm_calls"] == judge["convened"], "judge 子集每次开会恰好 1 次调用"
    assert full["tokens"] > judge["tokens"]


def test_variants_share_the_same_gate(tmp_path):
    """四个变体看同一行情：开会次数必须一致（否则成本/质量对比没有意义）。"""
    reports = run_direct(tmp_path, HoldLLM())
    convened = {reports[name]["convened"] for name in ca.VARIANTS}
    assert len(convened) == 1, f"各变体开会数不一致：{convened}"
    assert reports["full8"]["return_pct"] == reports["judge"]["return_pct"] == 0.0, \
        "全 HOLD 时账本不许动（不动的账本不许漂移）"


def test_consecutive_llm_failures_abort_the_variant(tmp_path):
    report = ca.run_variant("judge", ca.VARIANTS["judge"], "601988", make_records(),
                            "2026-02-01", "2026-03-01",
                            json.loads(json.dumps(ca.DEFAULT_CONFIG)),
                            completion=DeadLLM())
    assert report["error"] and "中止" in report["error"]
    # 中止线 = 连续 2 次：第 1 次失败的那天会留下一条 HOLD（与 evolve_replay 同口径），
    # 但绝不允许再往后静默跑出"18 天假 HOLD"的废轨迹。
    assert report["convened"] == ca.MAX_CONSECUTIVE_LLM_FAILURES
    assert report["decisions"]["hold"] <= 1


def test_every_variant_set_contains_the_judge():
    for name, roles in ca.VARIANTS.items():
        assert "risk_judge" in roles, f"变体 {name} 没有裁决者，跑不出决策"
        assert roles[-1] == "risk_judge", f"变体 {name} 的裁决者必须在最后一棒"


if __name__ == "__main__":
    import tempfile
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(tempfile.mkdtemp())
        print("PASS", fn.__name__)
