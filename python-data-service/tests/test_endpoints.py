import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
import akshare_client
import agent.tool_registry
import app as main

# 服务间鉴权：.env 里配了 INTERNAL_API_TOKEN 时，不带请求头会一律 401。
# 这些用例曾经因为没带头而"看起来像接口坏了"，其实请求压根没进业务逻辑。
TOKEN = getattr(main, "INTERNAL_API_TOKEN", "") or ""
HEADERS = {"X-Internal-Token": TOKEN} if TOKEN else {}

VALID_STRATEGY = {
    "schema_version": "1.0",
    "name": "ma",
    "symbol": "600519",
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}
    ]},
    "exit": {"logic": "any", "conditions": [
        {"type": "stop_loss_pct", "value": -8}
    ]},
}

def make_bars(n=30, start=100.0, step=1.0):
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

def test_validate_endpoint():
    c = TestClient(main.app)
    payload = {"strategy_json": VALID_STRATEGY}
    r = c.post("/api/v1/strategies/validate", json=payload, headers=HEADERS)
    assert r.status_code == 200
    assert r.json()["valid"] is True

def test_validate_rejects_non_object_strategy_json():
    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/validate", json={"strategy_json": []}, headers=HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is False
    assert body["error"] == "strategy_json must be an object"
    assert body["normalized"] is None

def test_backtest_with_fixed_history():
    c = TestClient(main.app)
    original = akshare_client.get_history
    akshare_client.get_history = lambda symbol, *args, **kwargs: make_bars()
    try:
        r = c.post("/api/v1/strategies/backtest", json={"strategy_json": VALID_STRATEGY}, headers=HEADERS)
    finally:
        akshare_client.get_history = original
    assert r.status_code == 200
    body = r.json()
    assert body["valid"] is True
    assert "backtest" in body

def test_evaluate_bar_empty_history():
    c = TestClient(main.app)
    original = akshare_client.get_history
    akshare_client.get_history = lambda symbol, *args, **kwargs: None
    try:
        r = c.post("/api/v1/strategies/evaluate-bar", json={"strategy_json": VALID_STRATEGY}, headers=HEADERS)
    finally:
        akshare_client.get_history = original
    assert r.status_code == 200
    assert "error" in r.json()


def test_agent_diagnostic_endpoint():
    c = TestClient(main.app)
    original = agent.tool_registry.execute_tool

    def fake_execute(name, args):
        if name == "get_risk_metrics":
            return {"risk_level": "low", "decision_note": "test"}
        if name == "get_model_status":
            return {"available": True, "decision_use": False}
        if name == "get_model_consensus":
            return {"consensus": "neutral", "confidence": "low", "decision_use": False}
        return {"error": "unexpected"}

    agent.tool_registry.execute_tool = fake_execute
    try:
        r = c.get("/api/v1/agent/diagnostic/600519", headers=HEADERS)
    finally:
        agent.tool_registry.execute_tool = original

    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "600519"
    assert body["risk"]["risk_level"] == "low"
    assert body["model_status"]["decision_use"] is False
    assert body["model_consensus"]["decision_use"] is False
    assert "disclaimer" in body


def test_health_reports_memory_subsystem():
    """健康检查要说清楚"语义召回到底在工作没有"。

    向量后端与 embedding 都是可缺失的增强：缺了不报错、只是静默退化成关键词检索，
    这类"功能静默降级"最难排查，所以状态必须能从 /health 一眼看到。
    """
    c = TestClient(main.app)
    r = c.get("/health")

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    memory = body["memory"]
    assert memory["vector_backend"] in ("numpy", "chroma")
    assert isinstance(memory["semantic_recall_enabled"], bool)
    assert memory["embedding_model"]
    assert "index" in memory


def test_health_survives_a_broken_memory_subsystem(monkeypatch):
    """子系统探测失败不能把整个健康检查带崩（那会让运维误判整个服务挂了）。"""
    from agent import vector_index

    monkeypatch.setattr(vector_index, "stats", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    c = TestClient(main.app)
    r = c.get("/health")

    assert r.status_code == 200
    assert r.json()["memory"] == {"error": "memory subsystem unavailable"}


def test_health_reports_the_objective_fact_channel():
    """客观事实通道（W1）必须能从 /health 一眼看到。

    为什么把键清单放进健康检查：它是**跨语言的契约**（Java 侧 ObjectiveFactKeys），
    两边名字对不上时取代链会静默失效 —— 摆到台面上，漂移就现形。
    逐字比对在 tests/test_objective_facts.py，这里只负责"它有没有被接进运行时自检"。
    """
    from agent import objective

    c = TestClient(main.app)
    r = c.get("/health")

    assert r.status_code == 200
    described = r.json()["objective_facts"]
    assert described["provenance"] == "system"
    assert described["trust"] == "high"
    assert described["confirmed"] is True
    assert described["predicates"] == list(objective.PREDICATES)


def test_health_reports_the_turn_audit():
    """一轮体检（W3）必须能从 /health 看到：查什么、以及**它不阻断主流程**。"""
    from agent import tool_audit

    c = TestClient(main.app)
    r = c.get("/health")

    assert r.status_code == 200
    described = r.json()["audit"]
    assert described["blocking"] is False
    assert described["checks"] == tool_audit.describe()["checks"]
    assert "unsupported_claim" in described["checks"]


def test_health_reports_the_reviewer():
    """裁决者必须能从 /health 看到"它没有工具、不阻断、判合法必须引证据"。"""
    from agent import review

    c = TestClient(main.app)
    r = c.get("/health")

    assert r.status_code == 200
    described = r.json()["review"]
    assert described["tools"] == []
    assert described["blocking"] is False
    assert described == review.describe()


def test_health_reports_strategy_review():
    """策略审查必须能从 /health 看到"哪一半是算术、哪一半靠模型"。"""
    from agent import strategy_review

    c = TestClient(main.app)
    r = c.get("/health")

    assert r.status_code == 200
    described = r.json()["strategy_review"]
    assert "sign_flipped_condition" in described["deterministic_checks"]
    assert described["evidence"] == "json_path"
    assert described["blocking"] is False


# ==================== evaluate-bar 的执行口径（契约的消费者） ====================

def test_evaluate_bar_endpoint_returns_basis_and_snapshot(monkeypatch):
    """端到端：HTTP 层必须把**口径与证据快照**带回来，而不是只回一个裸的 price。

    只有引擎层带快照是不够的 —— Java 是通过这个端点拿数据的，
    少了这一步，"这条成交属于哪个口径"仍然无从记录。
    """
    from agent import execution_contract as ec

    bars = make_bars(n=80)
    monkeypatch.setattr(akshare_client, "get_history", lambda symbol, *a, **k: bars)

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/evaluate-bar", headers=HEADERS,
               json={"strategy_json": VALID_STRATEGY, "settlement_kind": "daily",
                     "date": bars[-1]["date"]})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fill_basis"] == ec.FILL_CLOSE
    assert body["fills"]["close"] == bars[-1]["close"]
    assert body["snapshot"]["schema_version"] == ec.SNAPSHOT_SCHEMA_VERSION
    assert body["snapshot"]["bar"]["date"] == bars[-1]["date"]
    assert sorted(body["snapshot"]["indicators"]) == ec.indicator_keys_for(VALID_STRATEGY)
    # 复权口径来自取数处（akshare_client.ADJUST_MODE），随结果一起回来
    assert body["fingerprint"]["adjust_mode"] == akshare_client.ADJUST_MODE


def test_evaluate_bar_endpoint_honours_the_declared_settlement_kind(monkeypatch):
    from agent import execution_contract as ec

    bars = make_bars(n=80)
    monkeypatch.setattr(akshare_client, "get_history", lambda symbol, *a, **k: bars)

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/evaluate-bar", headers=HEADERS,
               json={"strategy_json": VALID_STRATEGY, "settlement_kind": "realtime",
                     "date": bars[-1]["date"]})

    assert r.json()["fill_basis"] == ec.FILL_REALTIME_LAST


def test_evaluate_bar_endpoint_without_data_still_reports_the_basis(monkeypatch):
    """没有数据时快照为空，但**口径仍要写清楚**：否则这条记录只能靠猜。"""
    from agent import execution_contract as ec

    monkeypatch.setattr(akshare_client, "get_history", lambda symbol, *a, **k: None)

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/evaluate-bar", headers=HEADERS,
               json={"strategy_json": VALID_STRATEGY, "settlement_kind": "daily"})

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["error"] == "insufficient history data"
    assert body["snapshot"] is None
    assert body["fill_basis"] == ec.FILL_CLOSE
    assert body["fingerprint"]["fill_basis"] == ec.FILL_CLOSE


# ==================== backtest-matrix：样本外验证的入口 ====================

def _matrix_payload(**overrides):
    payload = {"strategy_json": VALID_STRATEGY, "symbols": ["600519", "000001"], "segments": 3}
    payload.update(overrides)
    return payload


def test_backtest_matrix_returns_a_table_with_the_basis(monkeypatch):
    """端到端：这张表必须自带**口径**（复权方式、请求段数、请求标的）。

    一张不写口径的"多标的回测表"是不可解释的：换一次复权方式、换一次分段，
    同一份策略会给出不同的数字，而看表的人无从知道手里这张是哪一种。
    """
    monkeypatch.setattr(akshare_client, "get_history",
                        lambda symbol, *a, **k: make_bars(n=400, step=0.5))

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/backtest-matrix", headers=HEADERS, json=_matrix_payload())

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["valid"] is True
    assert body["symbols"] == ["600519", "000001"]
    assert body["adjust_mode"] == akshare_client.ADJUST_MODE
    assert body["segments_requested"] == 3
    assert body["per_symbol"][0]["segments_used"] == 3
    assert body["per_symbol"][0]["warmup_bars"] == 60
    assert len(body["per_symbol"]) == 2
    assert body["cells_valid"] > 0
    for cell in body["per_symbol"][0]["rows"]:
        assert "attribution" in cell, "每一格都要能回答'亏在方向还是亏在摩擦'"


def test_backtest_matrix_shrinks_the_segmentation_instead_of_faking_evidence(monkeypatch):
    """历史太短时**减少段数并说明**，而不是切出几段全在预热期里的"样本"。

    MA60 的策略要 60 根预热，150 根只够切 1 段（60 预热 + 30 可交易 的下限）。
    硬切 3 段的话每段 50 根 —— 一格都出不了信号，却在表上写着 0.00%，
    看起来像"验证过、这规则很稳"。
    """
    monkeypatch.setattr(akshare_client, "get_history",
                        lambda symbol, *a, **k: make_bars(n=150, step=0.5))

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/backtest-matrix", headers=HEADERS, json=_matrix_payload())
    body = r.json()

    first = body["per_symbol"][0]
    assert body["segments_requested"] == 3
    assert first["segments_used"] == 1
    assert "只能切 1 段" in first["note"]
    assert first["min_bars_per_segment"] == 90


def test_backtest_matrix_reports_a_symbol_without_data_instead_of_dropping_it(monkeypatch):
    """取不到数据的标的**留在表里**并写明原因 —— 静默丢行会让人以为覆盖了全部标的。"""
    monkeypatch.setattr(akshare_client, "get_history",
                        lambda symbol, *a, **k: None if symbol == "000001" else make_bars(n=400, step=0.5))

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/backtest-matrix", headers=HEADERS, json=_matrix_payload())

    body = r.json()
    assert body["valid"] is True
    assert body["symbols"] == ["600519", "000001"]
    blank = [item for item in body["per_symbol"] if item["symbol"] == "000001"][0]
    assert blank["note"] == "取不到行情数据"
    assert blank["rows"] == []


def test_backtest_matrix_caps_the_symbol_count(monkeypatch):
    """上限是可验证的纪律：多给的标的会被截断，而**请求列表照实回报**。"""
    seen = []

    def recording(symbol, *a, **k):
        seen.append(symbol)
        return make_bars(n=400, step=0.5)

    monkeypatch.setattr(akshare_client, "get_history", recording)

    symbols = [f"{600000 + i}" for i in range(main.MAX_MATRIX_SYMBOLS + 3)]
    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/backtest-matrix", headers=HEADERS,
               json=_matrix_payload(symbols=symbols))

    body = r.json()
    assert len(seen) == main.MAX_MATRIX_SYMBOLS
    assert body["requested_symbols"] == symbols
    assert body["symbols"] == symbols[:main.MAX_MATRIX_SYMBOLS]


def test_backtest_matrix_falls_back_to_the_strategy_symbol(monkeypatch):
    monkeypatch.setattr(akshare_client, "get_history",
                        lambda symbol, *a, **k: make_bars(n=400, step=0.5))

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/backtest-matrix", headers=HEADERS,
               json=_matrix_payload(symbols=[]))

    assert r.json()["symbols"] == [VALID_STRATEGY["symbol"]]


def test_backtest_matrix_rejects_a_bad_strategy(monkeypatch):
    monkeypatch.setattr(akshare_client, "get_history",
                        lambda symbol, *a, **k: make_bars(n=400, step=0.5))

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/backtest-matrix", headers=HEADERS,
               json=_matrix_payload(strategy_json={"entry": {}}))

    assert r.json()["valid"] is False


def test_backtest_matrix_clamps_segments(monkeypatch):
    """段数是取数成本与样本量的折中：给个荒唐的段数不该把服务打爆。"""
    monkeypatch.setattr(akshare_client, "get_history",
                        lambda symbol, *a, **k: make_bars(n=400, step=0.5))

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/backtest-matrix", headers=HEADERS,
               json=_matrix_payload(segments=999))

    body = r.json()
    assert body["valid"] is True
    assert body["segments_requested"] <= 12


def test_backtest_matrix_passes_the_date_range_to_the_source(monkeypatch):
    """区间必须**原样传给取数**：这正是"换一段行情还成不成立"的前提。

    这条用例是有来历的：区间曾经被接口的 `limit` 静默砍成"最近 500 根"，
    请求 2023 年起、实际只跑到 2024-08，而表上没有任何地方能看出来。
    """
    calls = []

    def recording(symbol, start_date="", end_date="", *a, **k):
        calls.append((symbol, start_date, end_date))
        return make_bars(n=400, step=0.5)

    monkeypatch.setattr(akshare_client, "get_history", recording)

    c = TestClient(main.app)
    r = c.post("/api/v1/strategies/backtest-matrix", headers=HEADERS,
               json=_matrix_payload(start_date="2023-01-01", end_date="2026-09-17"))

    body = r.json()
    assert calls and all(call[1] == "2023-01-01" and call[2] == "2026-09-17" for call in calls)
    assert body["start_date"] == "2023-01-01"
    assert body["end_date"] == "2026-09-17"
    # 表上要能看出**实际**覆盖到哪一天，而不是只写请求的区间
    assert body["per_symbol"][0]["first_date"] == make_bars(n=400, step=0.5)[0]["date"]
    assert body["per_symbol"][0]["last_date"] == make_bars(n=400, step=0.5)[-1]["date"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)