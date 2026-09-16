# -*- coding: utf-8 -*-
"""客观事实通道（W1）：契约、白名单、拒绝规则，以及**跨语言键一致性**。

<h3>这个文件在防什么</h3>
客观事实这条通道的全部价值建立在"**同一个东西永远落在同一个键上**"：
取代链靠键工作，一处写 `backtest_drawdown`、另一处写 `backtest_max_drawdown_pct`，
系统里就会出现两个"回撤"事实永远互相不取代 —— 每条单独看都对，合起来自相矛盾。
所以这里钉四件事：

1. **键的白名单**：未知谓词必须被丢弃（而不是宽容写入）；
2. **来源标注写死**：provenance=system / trust=high / confirmed=true / confidence=1.0
   —— 少一个，这条事实在注入时就会被标注成"未经确认"，与用户随口一说混在一起；
3. **跨语言一致**：Java 的 `ObjectiveFactKeys` 与 Python 的 `objective.PREDICATES`
   逐字相同（这个项目里"两边名字对不上就静默失效"已经发生过太多次）；
4. **缺字段不补 0**：假的 0 会取代掉上一次真实的回撤值。
"""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import memory_store
from agent import objective

JAVA_KEYS = (Path(__file__).resolve().parents[2]
             / "src" / "main" / "java" / "com" / "happyericsix" / "stocktracker"
             / "service" / "ObjectiveFactKeys.java")


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def _capture_post(monkeypatch, payload=None):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(method="POST", url=url, body=json, headers=headers, timeout=timeout)
        return _FakeResponse(payload if payload is not None else {"results": []})

    monkeypatch.setattr(memory_store.requests, "post", fake_post)
    return captured


# ==================== 1. 跨语言键一致性 ====================


def _java_predicates():
    """按标记注释切出 Java 侧的谓词常量段再解析。

    用标记而不是"扫全文件的字符串常量"：文件里还有 subject 前缀之类的字面量，
    整文件扫描会让测试对无关改动变得敏感，最后被人加个 `@SuppressWarnings` 绕过。
    """
    text = JAVA_KEYS.read_text(encoding="utf-8")
    assert ">>> OBJECTIVE_PREDICATES" in text and "<<< OBJECTIVE_PREDICATES" in text, \
        "Java 契约文件的标记注释被删了，跨语言一致性检查会静默失效"
    block = text.split(">>> OBJECTIVE_PREDICATES", 1)[1].split("<<< OBJECTIVE_PREDICATES", 1)[0]
    return [value for _name, value in
            re.findall(r'static final String ([A-Z0-9_]+)\s*=\s*"([^"]+)"', block)]


def test_java_contract_file_exists():
    assert JAVA_KEYS.exists(), f"Java 侧契约文件不见了：{JAVA_KEYS}"


def test_java_and_python_predicates_are_identical():
    java = _java_predicates()
    assert java, "没能从 Java 契约文件里解析出任何谓词"
    assert len(java) == len(set(java)), "Java 侧有重复的谓词"
    assert set(java) == set(objective.PREDICATES), (
        "两边谓词不一致 —— 差集："
        f"仅 Java 有 {sorted(set(java) - set(objective.PREDICATES))}，"
        f"仅 Python 有 {sorted(set(objective.PREDICATES) - set(java))}")


def test_predicate_naming_convention():
    """键名自带单位后缀，这样"18.3 是百分比还是小数"在键名里就答完了。"""
    for predicate in objective.PREDICATES:
        assert predicate == predicate.lower(), predicate
        assert re.fullmatch(r"[a-z][a-z0-9_]*", predicate), predicate
    assert "backtest_win_rate_pct" in objective.PREDICATES  # 引擎字段叫 win_rate，键名带 _pct


def test_strategy_subject_is_id_based_not_name_based():
    """策略事实挂数字 id：名称会改、会重名，取代链必须挂在稳定标识上。"""
    assert objective.strategy_subject(42) == "strategy:42"
    assert objective.strategy_subject("42") == "strategy:42"
    assert objective.strategy_subject(None) == ""
    assert objective.strategy_subject("  ") == ""


# ==================== 2. 构造器的拒绝规则 ====================


def test_build_fact_stamps_system_provenance():
    fact = objective.build_fact("strategy:42", "backtest_max_drawdown_pct", -18.3)
    assert fact["provenance"] == "system"
    assert fact["trust"] == "high"
    assert fact["confirmed"] is True
    assert fact["confidence"] == 1.0
    assert fact["fact_type"] == "observation"
    assert fact["object"] == "-18.3"


def test_unknown_predicate_is_dropped():
    """不能宽容写入：一条孤儿事实永远不会与其它值互相取代。"""
    assert objective.build_fact("strategy:42", "backtest_drawdown", -18.3) is None
    assert objective.build_fact("strategy:42", "", -18.3) is None


def test_missing_identity_or_value_is_dropped():
    assert objective.build_fact("", "backtest_sharpe", 0.8) is None
    assert objective.build_fact("strategy:42", "backtest_sharpe", None) is None


def test_structured_values_are_rejected():
    """客观事实是**一个标量**：结构化结果属于 offload 落盘，不是记忆。"""
    assert objective.build_fact("strategy:42", "backtest_sharpe", {"a": 1}) is None
    assert objective.build_fact("strategy:42", "backtest_sharpe", [1, 2]) is None


def test_float_precision_is_capped_at_three_decimals():
    """回测指标本来就带噪声，多留几位只会让取代链看起来"每天都在变"。"""
    assert objective.build_fact("strategy:1", "backtest_sharpe", 0.812345678)["object"] == "0.812"
    assert objective.build_fact("strategy:1", "backtest_trade_count", 8)["object"] == "8"
    assert objective.build_fact("strategy:1", "backtest_at", "2026-09-17T15:00:00")["object"] \
        == "2026-09-17T15:00:00"


def test_integral_floats_are_normalised_to_integers():
    """同一个量在 JSON 里可能是 7 也可能是 7.0。

    不归一就会出现"值没变、取代链却多了一条"的假变更 —— 而取代链正是
    "什么时候真的变了"这个问题的答案来源。这条规则与 Java 侧 format 必须一致。
    """
    assert objective.build_fact("strategy:1", "backtest_trade_count", 7.0)["object"] == "7"
    assert objective.build_fact("strategy:1", "paper_equity", 10000.0)["object"] == "10000"
    # 真正的分数不受影响
    assert objective.build_fact("strategy:1", "backtest_sharpe", 7.5)["object"] == "7.5"


def test_non_finite_values_are_rejected():
    """NaN/Infinity 不是可比较的观测：写进去只会取代掉一个真实的值。"""
    assert objective.build_fact("strategy:1", "backtest_sharpe", float("nan")) is None
    assert objective.build_fact("strategy:1", "backtest_sharpe", float("inf")) is None


def test_long_values_are_clamped():
    fact = objective.build_fact("strategy:1", "backtest_at", "x" * 500)
    assert len(fact["object"]) == objective.MAX_VALUE_CHARS


def test_unknown_fact_type_falls_back_to_observation():
    fact = objective.build_fact("strategy:1", "backtest_sharpe", 1.0, fact_type="preference")
    assert fact["fact_type"] == "observation"
    assert objective.build_fact("strategy:1", "backtest_sharpe", 1.0,
                                fact_type="decision")["fact_type"] == "decision"


def test_describe_reports_the_contract():
    described = objective.describe()
    assert described["provenance"] == "system"
    assert described["trust"] == "high"
    assert described["confirmed"] is True
    assert described["predicates"] == list(objective.PREDICATES)
    assert described["subjects"]["strategy"] == "strategy:<数字 id>"


# ==================== 3. 写入：请求形状与失败纪律 ====================


def test_record_facts_request_shape(monkeypatch):
    """Python 内部是 snake_case，Java 侧是 camelCase —— 这层映射错了就静默丢来源标注。"""
    captured = _capture_post(monkeypatch, {"results": [{"id": 57, "action": "created"}]})

    fact = objective.build_fact("strategy:42", "backtest_max_drawdown_pct", -18.3)
    results = objective.record_facts(7, "7:2026-09-17", [fact])

    assert captured["url"].endswith("/api/v1/internal/memory/facts")
    body = captured["body"]
    assert body["userId"] == 7
    assert body["sessionKey"] == "7:2026-09-17"
    sent = body["facts"][0]
    assert sent["subject"] == "strategy:42"
    assert sent["predicate"] == "backtest_max_drawdown_pct"
    assert sent["provenance"] == "system"
    assert sent["trust"] == "high"
    assert sent["confirmed"] is True
    assert sent["confidence"] == 1.0
    assert sent["factType"] == "observation"
    assert results == [{"id": 57, "action": "created"}]


def test_record_facts_without_identity_makes_no_request(monkeypatch):
    """没有身份就没有记忆：不写孤儿事实（查不出是谁的观测）。"""
    captured = _capture_post(monkeypatch)

    fact = objective.build_fact("strategy:42", "backtest_sharpe", 1.0)
    assert objective.record_facts(None, "7:2026-09-17", [fact]) == []
    assert objective.record_facts(7, "", [fact]) == []
    assert objective.record_facts(7, "7:2026-09-17", []) == []
    assert "url" not in captured


def test_record_facts_never_raises(monkeypatch):
    """记忆是增强功能：回测/结算绝不能因为记忆写不进去而失败。"""
    def boom(url, json=None, headers=None, timeout=None):
        raise RuntimeError("java 挂了")

    monkeypatch.setattr(memory_store.requests, "post", boom)

    fact = objective.build_fact("strategy:42", "backtest_sharpe", 1.0)
    assert objective.record_facts(7, "7:2026-09-17", [fact]) == []


def test_record_facts_tolerates_unexpected_response_shape(monkeypatch):
    _capture_post(monkeypatch, {"results": "oops"})

    fact = objective.build_fact("strategy:42", "backtest_sharpe", 1.0)
    assert objective.record_facts(7, "7:2026-09-17", [fact]) == []


# ==================== 4. 领域写入器：缺字段不补 0 ====================


def test_record_backtest_maps_engine_fields(monkeypatch):
    captured = _capture_post(monkeypatch, {"results": []})

    objective.record_backtest(7, "7:2026-09-17", 42, {
        "total_return_pct": 12.5,
        "buy_and_hold_return_pct": 3.25,
        "excess_return_pct": 9.25,
        "max_drawdown_pct": -18.3,
        "sharpe_ratio": 0.812,
        "win_rate": 42.5,
        "trade_count": 8,
    }, ran_at="2026-09-17T15:00:00")

    sent = {fact["predicate"]: fact["object"] for fact in captured["body"]["facts"]}
    assert sent["backtest_total_return_pct"] == "12.5"
    assert sent["backtest_buy_and_hold_return_pct"] == "3.25"
    assert sent["backtest_excess_return_pct"] == "9.25"
    assert sent["backtest_max_drawdown_pct"] == "-18.3"
    # 引擎字段是 win_rate（没有 _pct 后缀），但单位是百分比 —— 映射只此一处
    assert sent["backtest_win_rate_pct"] == "42.5"
    assert sent["backtest_trade_count"] == "8"
    assert sent["backtest_at"] == "2026-09-17T15:00:00"
    for fact in captured["body"]["facts"]:
        assert fact["subject"] == "strategy:42"
        assert fact["provenance"] == "system"


def test_record_backtest_does_not_zero_missing_fields(monkeypatch):
    """一个假的 0 会取代掉上一次真实的回撤值 —— 比"这次没记录"危险得多。"""
    captured = _capture_post(monkeypatch, {"results": []})

    objective.record_backtest(7, "7:2026-09-17", 42, {"total_return_pct": 1.5})

    predicates = [fact["predicate"] for fact in captured["body"]["facts"]]
    assert predicates == ["backtest_total_return_pct"]


def test_record_backtest_ignores_non_numeric_and_non_dict(monkeypatch):
    """字符串（"N/A"）、布尔、NaN 都不是观测值：写进去会取代掉真实数字。"""
    captured = _capture_post(monkeypatch, {"results": []})

    objective.record_backtest(7, "7:2026-09-17", 42, None)
    assert "url" not in captured

    objective.record_backtest(7, "7:2026-09-17", 42, {"max_drawdown_pct": "N/A"})
    assert "url" not in captured

    objective.record_backtest(7, "7:2026-09-17", 42, {"trade_count": True,
                                                      "sharpe_ratio": float("nan")})
    assert "url" not in captured


def test_record_paper_snapshot_computes_return_pct(monkeypatch):
    """收益率口径只此一处：初始本金是账户自己的字段，不让调用方各算一遍。"""
    captured = _capture_post(monkeypatch, {"results": []})

    objective.record_paper_snapshot(7, "7:2026-09-17", 42, {
        "equity": 97000.0,
        "cash": 0.0,
        "shares": 100.0,
        "initial_capital": 100000.0,
    }, as_of="2026-09-17T15:00:00")

    sent = {fact["predicate"]: fact["object"] for fact in captured["body"]["facts"]}
    assert sent["paper_equity"] == "97000"
    assert sent["paper_cash"] == "0"
    assert sent["paper_shares"] == "100"
    assert sent["paper_return_pct"] == "-3"
    assert sent["paper_last_eval_at"] == "2026-09-17T15:00:00"


def test_record_paper_snapshot_skips_return_without_capital(monkeypatch):
    captured = _capture_post(monkeypatch, {"results": []})

    objective.record_paper_snapshot(7, "7:2026-09-17", 42, {"equity": 97000.0})

    predicates = [fact["predicate"] for fact in captured["body"]["facts"]]
    assert "paper_return_pct" not in predicates
    assert "paper_equity" in predicates


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
