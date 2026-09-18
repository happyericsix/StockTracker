import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import agent.tool_registry as registry
from agent import tool_contract as tc
from agent.tool_registry import TOOL_SCHEMAS, TOOL_TIMEOUTS, execute_tool


def test_schemas_have_names():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert {
        "search_stock", "get_quote", "get_history", "get_indicators",
        "get_risk_metrics", "validate_strategy", "backtest_strategy",
        "finalize_strategy", "get_model_status", "get_model_consensus",
    } <= names


def test_every_tool_has_a_timeout_budget():
    """per-tool 超时：新增工具必须显式给出预算，不能悄悄退回一刀切的默认值。"""
    for name in [t["function"]["name"] for t in TOOL_SCHEMAS]:
        assert name in TOOL_TIMEOUTS, f"{name} 没有配置超时预算"
        assert TOOL_TIMEOUTS[name] > 0


def test_results_are_envelopes():
    """所有工具、包括出错路径，都必须返回统一信封。"""
    out = execute_tool("validate_strategy", {"strategy_json": {
        "schema_version": "1.0", "name": "x", "symbol": "600519",
        "entry": {"logic": "all", "conditions": [{"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]},
        "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]}}})
    assert tc.is_envelope(out)
    assert out["ok"] is True
    assert out["data"]["valid"] is True

    unknown = execute_tool("nope", {})
    assert unknown["ok"] is False
    assert unknown["error"]["code"] == tc.UNKNOWN_TOOL

    bad_args = execute_tool("get_quote", "600519")
    assert bad_args["ok"] is False
    assert bad_args["error"]["code"] == tc.INVALID_ARGS


def test_missing_symbol_is_a_structured_error():
    """过去 get_quote("") 会返回 {"quote": None}，模型只能猜发生了什么。"""
    out = execute_tool("get_quote", {})
    assert out["ok"] is False
    assert out["error"]["code"] == tc.INVALID_ARGS
    assert "symbol" in out["error"]["message"]


def _fake_bars(n=80, start=100.0, step=1.0):
    bars = []
    for i in range(n):
        close = start + step * i
        bars.append({
            "date": f"2026-01-{i+1:02d}",
            "open": close,
            "high": close + 2,
            "low": close - 2,
            "close": close,
            "volume": 1000,
        })
    return bars


def test_indicators_and_risk_tools():
    original = registry.get_history
    registry.get_history = lambda symbol: _fake_bars()
    try:
        indicators = tc.data_of(execute_tool("get_indicators", {"symbol": "600519"}))
        risk = tc.data_of(execute_tool("get_risk_metrics", {"symbol": "600519"}))
    finally:
        registry.get_history = original

    assert indicators["rsi14"] is not None
    assert "risk_level" in risk
    assert "decision_note" in risk


def test_insufficient_history_is_structured():
    original = registry.get_history
    registry.get_history = lambda symbol: _fake_bars(5)
    try:
        out = execute_tool("get_indicators", {"symbol": "600519"})
    finally:
        registry.get_history = original

    assert out["ok"] is False
    assert out["error"]["code"] == tc.INSUFFICIENT_DATA


def test_history_is_compact_and_capped():
    """过去 250 根 K 线原样进上下文；现在条数封顶 + 只保留 OHLCV 必需字段。"""
    bars = [{
        "date": f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}",
        "open": 100.0 + i, "high": 102.0 + i, "low": 98.0 + i,
        "close": 101.0 + i, "volume": 1000 + i,
    } for i in range(400)]

    original = registry.get_history
    registry.get_history = lambda symbol: bars
    try:
        out = execute_tool("get_history", {"symbol": "600519", "days": 400})
    finally:
        registry.get_history = original

    assert out["ok"] is True
    data = out["data"]
    assert data["count"] == registry.HISTORY_MAX_DAYS
    assert data["total_available"] == 400
    assert data["last_date"] == bars[-1]["date"]
    assert data["first_date"] == bars[-registry.HISTORY_MAX_DAYS]["date"]
    assert set(data["records"][-1]) == {"date", "open", "high", "low", "close", "volume"}


def test_get_history_rejects_non_numeric_days():
    original = registry.get_history
    registry.get_history = lambda symbol: _fake_bars()
    try:
        out = execute_tool("get_history", {"symbol": "600519", "days": "最近一年"})
    finally:
        registry.get_history = original

    assert out["ok"] is False
    assert out["error"]["code"] == tc.INVALID_ARGS


def test_model_tools_are_diagnostic_only():
    original = registry.get_history
    registry.get_history = lambda symbol: []
    try:
        status = tc.data_of(execute_tool("get_model_status", {"symbol": "600519"}))
        consensus = tc.data_of(execute_tool("get_model_consensus", {"symbol": "600519"}))
    finally:
        registry.get_history = original

    for out in (status, consensus):
        assert out["decision_use"] is False
        assert out["confidence"] == "low"
        assert out["consensus"] == "neutral"


def test_tool_exception_becomes_envelope():
    """工具内部抛异常不能穿透到 agent 循环。"""
    original = registry.get_quote

    def boom(symbol):
        raise RuntimeError("network down")

    registry.get_quote = boom
    try:
        out = execute_tool("get_quote", {"symbol": "600519"})
    finally:
        registry.get_quote = original

    assert tc.is_envelope(out)
    assert out["ok"] is False
    assert out["error"]["code"] == tc.TOOL_ERROR
    assert out["error"]["retryable"] is True
    assert "network down" in out["error"]["message"]


# ==================== backtest_matrix：给模型的样本外验证 ====================

MATRIX_STRATEGY = {
    "schema_version": "1.0", "name": "上穿60日线", "symbol": "600519",
    "initial_capital": 100000,
    "entry": {"logic": "all", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "above"}]},
    "exit": {"logic": "any", "conditions": [
        {"type": "price_cross_ma", "window": 60, "direction": "below"}]},
}


def _matrix_bars(n=400, step=0.6):
    bars = []
    for i in range(n):
        close = 100.0 + step * i
        bars.append({"date": f"2026-{i // 28 + 1:02d}-{i % 28 + 1:02d}",
                     "open": close, "high": close + 2, "low": close - 2,
                     "close": close, "volume": 1000})
    return bars


def _with_history(fake):
    """临时替换注册表里的取数函数（工具直接 import 了它，所以要补在模块上）。"""
    class _Patch:
        def __enter__(self):
            self.original = registry.get_history
            registry.get_history = fake
            return self

        def __exit__(self, *exc):
            registry.get_history = self.original
            return False

    return _Patch()


def test_backtest_matrix_tool_returns_the_judgeable_numbers():
    """工具回的是"能直接判断的那几个数"，不是整张明细表。

    明细（每格十几个字段）会吃掉上下文；而判断"这条规则行不行"只需要：
    跑赢买入持有的格子数、平均超额、摩擦占本金的比、以及有几格其实没样本。
    """
    with _with_history(lambda symbol, *a, **k: _matrix_bars()):
        out = execute_tool("backtest_matrix", {
            "strategy_json": MATRIX_STRATEGY, "symbols": ["600519", "000001"], "segments": 3})

    assert out["ok"] is True, out
    data = out["data"]
    assert data["symbols"] == ["600519", "000001"]
    assert data["adjust_mode"] == registry.ADJUST_MODE
    assert data["cells_total"] == 6
    assert 0 <= data["beat_buy_and_hold"] <= data["cells_valid"]
    assert data["avg_cost_pct_of_capital"] is not None
    assert data["total_fees"] is not None
    # 没样本的格子必须单独报，否则模型会把它们读成"表现平平"
    assert "cells_unaffordable" in data and "cells_warmup_only" in data
    assert "没有样本" in data["note"]
    assert len(data["per_symbol"]) == 2
    assert "rows" not in data, "明细不该进上下文"


def test_backtest_matrix_tool_caps_the_symbols_it_will_actually_run():
    """模型给多少标的都行，但真正跑的不会超过上限 —— 而且**它自己知道**跑了几只。"""
    seen = []

    def recording(symbol, *a, **k):
        seen.append(symbol)
        return _matrix_bars()

    with _with_history(recording):
        out = execute_tool("backtest_matrix", {
            "strategy_json": MATRIX_STRATEGY,
            "symbols": ["600519", "000001", "600036", "601318", "002594", "600030", "000651"],
            "segments": 2})

    assert len(seen) == registry.MATRIX_TOOL_MAX_SYMBOLS
    assert out["data"]["symbols"] == ["600519", "000001", "600036", "601318", "002594", "600030"]
    assert out["data"]["segments_requested"] == 2


def test_backtest_matrix_tool_falls_back_to_the_strategy_symbol():
    with _with_history(lambda symbol, *a, **k: _matrix_bars()):
        out = execute_tool("backtest_matrix", {"strategy_json": MATRIX_STRATEGY})
    assert out["data"]["symbols"] == ["600519"]


def test_backtest_matrix_tool_reports_missing_data_as_a_structured_failure():
    with _with_history(lambda symbol, *a, **k: None):
        out = execute_tool("backtest_matrix", {
            "strategy_json": MATRIX_STRATEGY, "symbols": ["600519", "000001"]})

    assert out["ok"] is False
    assert out["error"]["code"] == tc.INSUFFICIENT_DATA


def test_backtest_matrix_tool_keeps_a_symbol_without_data_in_the_table():
    """一只票取不到数据，其余照跑 —— 静默少一行会让人以为覆盖了全部标的。"""
    with _with_history(lambda symbol, *a, **k: None if symbol == "000001" else _matrix_bars()):
        out = execute_tool("backtest_matrix", {
            "strategy_json": MATRIX_STRATEGY, "symbols": ["600519", "000001"]})

    blank = [row for row in out["data"]["per_symbol"] if row["symbol"] == "000001"][0]
    assert blank["note"] == "取不到行情数据"


def test_backtest_matrix_tool_rejects_a_bad_strategy():
    with _with_history(lambda symbol, *a, **k: _matrix_bars()):
        out = execute_tool("backtest_matrix", {"strategy_json": {"entry": {}}})
    assert out["ok"] is True
    assert out["data"]["valid"] is False


def test_backtest_matrix_cache_key_covers_what_will_actually_run():
    """缓存键少了标的或区间，就会出现"A 股的结果回答 B 股的问题"。

    键描述的是"将要发生的那次调用"，所以标的顺序不该影响它，标的本身必须影响它。
    """
    base = {"strategy_json": MATRIX_STRATEGY, "symbols": ["000001", "600519"]}
    flipped = {**base, "symbols": ["600519", "000001"]}
    assert registry._matrix_cache_key(base) == registry._matrix_cache_key(flipped)

    other_symbols = {**base, "symbols": ["600519"]}
    assert registry._matrix_cache_key(base) != registry._matrix_cache_key(other_symbols)

    other_segments = {**base, "segments": 2}
    assert registry._matrix_cache_key(base) != registry._matrix_cache_key(other_segments)

    other_range = {**base, "start_date": "2023-01-01"}
    assert registry._matrix_cache_key(base) != registry._matrix_cache_key(other_range)


# ==================== T0：声明式注册表的契约 ====================

def test_registry_self_check_passes():
    """声明之间不能自相矛盾（有副作用却没审批、不可逆却可重试、缓存键写错字段……）。

    这是"契约"能落地的关键：字段存在不等于有人检查它们互不冲突。
    """
    assert registry.validate() == []


def test_derived_exports_stay_consistent():
    """TOOL_SCHEMAS / TOOL_TIMEOUTS / handler 表必须全部来自同一份声明。

    改造前它们是三个各自维护的字面量，加一个工具要改三处、漏一处就静默不一致。
    """
    schema_names = [schema["function"]["name"] for schema in registry.TOOL_SCHEMAS]

    assert schema_names == registry.TOOL_NAMES
    assert set(registry.TOOL_TIMEOUTS) == set(schema_names)
    # ToolSpec 里有 dict（parameters）→ 不能塞进 set，逐个断言存在即可
    assert all(registry.get_spec(name) is not None for name in schema_names)


def test_every_tool_declares_namespace_layer_and_scope():
    for spec in registry.REGISTRY.specs():
        assert spec.namespace, f"{spec.name} 没有命名空间（T1 按技能装填要用它）"
        assert spec.layer in ("L1", "L2", "L3"), f"{spec.name} 分层非法"
        assert spec.timeout_s > 0
        # L1 纯计算层不该有网络语义；L2/L3 必须声明权限
        if spec.layer == "L1":
            assert spec.timeout_s <= 10, f"{spec.name} 标成 L1 却给了网络级超时"
        else:
            assert spec.scopes, f"{spec.name} 没有声明 scopes"


def test_default_user_scopes_cover_available_tools():
    """默认权限必须覆盖当前所有工具，否则升级到策略层会把功能悄悄关掉。"""
    for spec in registry.REGISTRY.specs():
        for scope in spec.scopes or ():
            assert scope in registry.DEFAULT_USER_SCOPES, f"{spec.name} 需要的 {scope} 不在默认权限里"


def test_enrollment_by_tool_set_and_tag():
    """按工具集装填（T1 接进循环）：让上下文里只出现这一轮需要的工具定义。"""
    only_quote = registry.schemas(enabled=["get_quote", "get_history"])

    assert [schema["function"]["name"] for schema in only_quote] == ["get_quote", "get_history"]
    assert "quote" in registry.REGISTRY.namespaces()
    assert set(registry.REGISTRY.by_tag("strategy")) >= {"validate_strategy", "backtest_strategy"}


def test_schemas_still_declare_only_supported_types():
    allowed = {"string", "integer", "number", "boolean", "object", "array"}
    for schema in registry.TOOL_SCHEMAS:
        for name, rule in (schema["function"]["parameters"].get("properties") or {}).items():
            assert rule.get("type") in allowed, f"{schema['function']['name']}.{name} 类型不受支持"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)
