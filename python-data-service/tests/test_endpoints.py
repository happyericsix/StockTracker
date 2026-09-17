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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("PASS", fn.__name__)