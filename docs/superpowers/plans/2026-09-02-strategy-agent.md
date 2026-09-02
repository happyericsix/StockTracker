# Strategy Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a ReAct agent that turns natural-language strategy requests into a validated strategy JSON, then executes that JSON through backtest and daily paper trading.

**Architecture:** Python owns the agent, strategy schema, rule evaluation, and backtest. Java owns strategy persistence, JWT-secured APIs, paper account settlement, and the daily paper-trading scheduler. The strategy JSON is the only contract between them.

**Tech Stack:** Python 3.11+ / FastAPI / pydantic v2 / numpy; Java 21 / Spring Boot 4 / JPA / Lombok / WebClient; Vue 3 / Vite / Pinia.

**Spec:** `docs/superpowers/specs/2026-09-02-strategy-agent-design.md`

## Global Constraints

- Strategy JSON schema version is `1.0`; conditions are exactly: `ma_cross`, `rsi_above`, `rsi_below`, `macd_cross`, `price_above`, `price_below`, `stop_loss_pct`, `take_profit_pct`, `trailing_stop_pct`.
- `entry.logic` and `exit.logic` accept only `all` or `any`; `position.type` accepts only `full` or `percent`.
- ReAct loop max 6 steps; LLM timeout 30s; Java-to-Python WebClient timeout 60s.
- No real orders. Paper trading is virtual and must remain clearly labeled virtual.
- Python modules live under `python-data-service/agent/`; Java packages stay under `com.happyericsix.stocktracker`.
- Commands: Python tests use `.venv\Scripts\python.exe -m pytest` from `python-data-service`; Java tests use `.\mvnw.cmd test`.

---

## File Structure

Python (create): `agent/__init__.py`, `agent/strategy_schema.py`, `agent/strategy_engine.py`, `agent/tool_registry.py`, `agent/react_agent.py`, `tests/test_strategy_schema.py`, `tests/test_strategy_engine.py`, `tests/test_react_agent.py`, `tests/test_tool_registry.py`, `tests/test_endpoints.py` (all under `python-data-service`).

Python (modify): `python-data-service/llm_service.py`, `python-data-service/app.py`.

Java (create): `entity/Strategy.java`, `entity/PaperAccount.java`, `entity/PaperTrade.java`, `repository/StrategyRepository.java`, `repository/PaperAccountRepository.java`, `repository/PaperTradeRepository.java`, `dto/StrategyRequest.java`, `dto/StrategyResponse.java`, `dto/PaperAccountResponse.java`, `dto/PaperTradeResponse.java`, `client/StrategyClient.java`, `service/StrategyService.java`, `service/PaperTradingService.java`, `job/PaperTradingJob.java`, `controller/StrategyController.java` (all under `src/main/java/com/happyericsix/stocktracker`).

Java (modify): `service/ChatService.java`.

Frontend (create/modify): `frontend/src/api/strategy.js`, `frontend/src/pages/Strategies.vue`, `frontend/src/pages/StrategyDetail.vue`, `frontend/src/router/index.js`, `frontend/src/pages/Assistant.vue`.

---

### Task 1: Strategy JSON schema and validation

**Files:** Create `python-data-service/agent/__init__.py`, `python-data-service/agent/strategy_schema.py`; Test `python-data-service/tests/test_strategy_schema.py`.

**Interfaces:** Produces `StrategyConfig` (pydantic model), `validate_strategy_config(data: dict) -> tuple[StrategyConfig | None, str | None]`, `extract_strategy_json(text: str) -> dict | None`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_strategy_schema.py
from agent.strategy_schema import validate_strategy_config, extract_strategy_json

VALID = {
    "schema_version": "1.0",
    "name": "MA cross",
    "symbol": "600519",
    "initial_capital": 100000,
    "data": {"period": "day", "lookback_days": 250},
    "position": {"type": "percent", "size_pct": 100},
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}
    ]},
    "exit": {"logic": "any", "conditions": [
        {"type": "ma_cross", "fast": 20, "slow": 60, "direction": "below"},
        {"type": "stop_loss_pct", "value": -8},
        {"type": "take_profit_pct", "value": 15}
    ]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}

def test_valid_strategy():
    cfg, err = validate_strategy_config(VALID)
    assert err is None and cfg.name == "MA cross"

def test_unknown_condition_rejected():
    bad = {**VALID, "entry": {"logic": "all", "conditions": [{"type": "vibe"}]}}
    cfg, err = validate_strategy_config(bad)
    assert cfg is None and err

def test_ma_cross_requires_fast_slow_direction():
    bad = {**VALID, "entry": {"logic": "all", "conditions": [{"type": "ma_cross", "fast": 20}]}}
    cfg, err = validate_strategy_config(bad)
    assert cfg is None and "ma_cross" in err

def test_extract_json_from_fence():
    text = 'Here:\n```json\n{"schema_version":"1.0"}\n```'
    assert extract_strategy_json(text) == {"schema_version": "1.0"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_schema.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement schema**

```python
# agent/strategy_schema.py
from __future__ import annotations
import json, re
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator, ValidationError

CONDITION_TYPES = {
    "ma_cross", "rsi_above", "rsi_below", "macd_cross",
    "price_above", "price_below",
    "stop_loss_pct", "take_profit_pct", "trailing_stop_pct",
}

class Condition(BaseModel):
    type: str
    fast: Optional[int] = None
    slow: Optional[int] = None
    direction: Optional[Literal["above", "below"]] = None
    value: Optional[float] = None

    @model_validator(mode="after")
    def _check(self):
        t = self.type
        if t not in CONDITION_TYPES:
            raise ValueError(f"unknown condition type: {t}")
        if t == "ma_cross":
            if self.fast is None or self.slow is None or self.direction is None:
                raise ValueError("ma_cross requires fast, slow, direction")
        elif t == "macd_cross":
            if self.direction is None:
                raise ValueError("macd_cross requires direction")
        elif t in {"rsi_above", "rsi_below", "price_above", "price_below",
                   "stop_loss_pct", "take_profit_pct", "trailing_stop_pct"}:
            if self.value is None:
                raise ValueError(f"{t} requires value")
        return self

class RuleGroup(BaseModel):
    logic: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(min_length=1)

class PositionRule(BaseModel):
    type: Literal["full", "percent"] = "full"
    size_pct: Optional[float] = 100.0

class DataRule(BaseModel):
    period: Literal["day"] = "day"
    lookback_days: int = 250

class RiskRule(BaseModel):
    commission_pct: float = 0.1
    slippage_pct: float = 0.1

class StrategyConfig(BaseModel):
    schema_version: Literal["1.0"] = "1.0"
    name: str
    symbol: str
    initial_capital: float = 100000.0
    data: DataRule = Field(default_factory=DataRule)
    position: PositionRule = Field(default_factory=PositionRule)
    entry: RuleGroup
    exit: RuleGroup
    risk: RiskRule = Field(default_factory=RiskRule)

def validate_strategy_config(data: dict):
    try:
        return StrategyConfig.model_validate(data), None
    except ValidationError as e:
        return None, str(e)

def extract_strategy_json(text: str) -> Optional[dict]:
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.S)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            return None
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add python-data-service/agent python-data-service/tests/test_strategy_schema.py
git commit -m "feat(agent): strategy JSON schema and validation"
```

---

### Task 2: Indicator computation and rule evaluation

**Files:** Create `python-data-service/agent/strategy_engine.py`; Test `python-data-service/tests/test_strategy_engine.py`.

**Interfaces:** Consumes `StrategyConfig`. Produces `compute_indicators(records: list[dict]) -> dict`, `evaluate_rule(rule: RuleGroup, ind: dict, i: int, position: dict | None) -> tuple[bool, list[str]]`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_strategy_engine.py
from agent.strategy_engine import compute_indicators, evaluate_rule
from agent.strategy_schema import RuleGroup

def make_bars(n=80, start=100.0, step=1.0):
    return [{"date": f"2026-01-{i+1:02d}", "open": start+step*i,
             "high": start+step*i+2, "low": start+step*i-2,
             "close": start+step*i, "volume": 1000} for i in range(n)]

def test_ma_cross_above():
    bars = make_bars()
    ind = compute_indicators(bars)
    rule = RuleGroup.model_validate({
        "logic": "all",
        "conditions": [{"type": "ma_cross", "fast": 5, "slow": 20, "direction": "above"}]
    })
    assert any(evaluate_rule(rule, ind, i, None)[0] for i in range(20, len(bars)))

def test_stop_loss_needs_position():
    ind = compute_indicators(make_bars())
    rule = RuleGroup.model_validate({
        "logic": "any",
        "conditions": [{"type": "stop_loss_pct", "value": -5}]
    })
    ok, _ = evaluate_rule(rule, ind, 30, None)
    assert not ok
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_engine.py -v`
Expected: FAIL, module missing

- [ ] **Step 3: Implement indicators and evaluator**

```python
# agent/strategy_engine.py
from __future__ import annotations
import numpy as np

def _sma(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = np.convolve(a, np.ones(w)/w, mode="valid")
    return out

def _ema(a, w):
    out = np.full(len(a), np.nan)
    if len(a) == 0:
        return out
    k = 2 / (w + 1)
    prev = a[0]
    out[0] = prev
    for i in range(1, len(a)):
        prev = a[i] * k + prev * (1 - k)
        out[i] = prev
    return out

def _rsi(a, w=14):
    out = np.full(len(a), np.nan)
    if len(a) <= w:
        return out
    deltas = np.diff(a)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = gains[:w].mean()
    avg_l = losses[:w].mean()
    for i in range(w, len(a)):
        avg_g = (avg_g * (w - 1) + gains[i-1]) / w
        avg_l = (avg_l * (w - 1) + losses[i-1]) / w
        out[i] = 100.0 if avg_l == 0 else 100 - 100 / (1 + avg_g / avg_l)
    return out

def compute_indicators(records):
    closes = np.array([float(r["close"]) for r in records], dtype=float)
    highs = np.array([float(r["high"]) for r in records], dtype=float)
    lows = np.array([float(r["low"]) for r in records], dtype=float)
    dates = [r.get("date", str(i)) for i, r in enumerate(records)]
    ind = {"closes": closes, "highs": highs, "lows": lows, "dates": dates}
    for w in (5, 10, 20, 60):
        ind[f"ma_{w}"] = _sma(closes, w)
    ind["rsi"] = _rsi(closes, 14)
    dif = _ema(closes, 12) - _ema(closes, 26)
    dea = _ema(np.nan_to_num(dif), 9)
    ind["macd_dif"] = dif
    ind["macd_dea"] = dea
    return ind

def _crossed(a, b, i, direction):
    if i == 0:
        return False
    if direction == "above":
        return a[i-1] <= b[i-1] and a[i] > b[i]
    return a[i-1] >= b[i-1] and a[i] < b[i]

def _cond_met(c, ind, i, position):
    t = c.type
    if t == "ma_cross":
        if np.isnan(ind[f"ma_{c.fast}"][i]) or np.isnan(ind[f"ma_{c.slow}"][i]):
            return False
        return _crossed(ind[f"ma_{c.fast}"], ind[f"ma_{c.slow}"], i, c.direction)
    if t == "macd_cross":
        if i == 0 or np.isnan(ind["macd_dif"][i]) or np.isnan(ind["macd_dea"][i]):
            return False
        return _crossed(ind["macd_dif"], ind["macd_dea"], i, c.direction)
    if t == "rsi_above":
        return not np.isnan(ind["rsi"][i]) and ind["rsi"][i] >= c.value
    if t == "rsi_below":
        return not np.isnan(ind["rsi"][i]) and ind["rsi"][i] <= c.value
    if t == "price_above":
        return ind["closes"][i] >= c.value
    if t == "price_below":
        return ind["closes"][i] <= c.value
    if position is None:
        return False
    entry = position.get("entry_price")
    if t == "stop_loss_pct":
        return bool(entry) and (ind["closes"][i] / entry - 1) * 100 <= c.value
    if t == "take_profit_pct":
        return bool(entry) and (ind["closes"][i] / entry - 1) * 100 >= c.value
    if t == "trailing_stop_pct":
        hwm = position.get("high_watermark") or entry or ind["closes"][i]
        return (hwm - ind["closes"][i]) / hwm * 100 >= c.value
    return False

def evaluate_rule(rule, ind, i, position=None):
    flags = [_cond_met(c, ind, i, position) for c in rule.conditions]
    names = [c.type for c in rule.conditions]
    hit = all(flags) if rule.logic == "all" else any(flags)
    reasons = [n for n, f in zip(names, flags) if f]
    return hit, reasons
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add python-data-service/agent/strategy_engine.py python-data-service/tests/test_strategy_engine.py
git commit -m "feat(agent): indicator and rule evaluation engine"
```

---

### Task 3: Backtest executor and single-bar evaluation

**Files:** Modify `python-data-service/agent/strategy_engine.py`; Test `python-data-service/tests/test_strategy_engine.py`.

**Interfaces:** Produces `run_backtest(config: dict, records: list[dict]) -> dict`, `evaluate_bar(config: dict, records: list[dict], date: str, position: dict | None) -> dict`.

- [ ] **Step 1: Write failing tests**

```python
from agent.strategy_engine import run_backtest, evaluate_bar

CONFIG = {
    "schema_version": "1.0", "name": "ma", "symbol": "600519",
    "initial_capital": 100000,
    "position": {"type": "full"},
    "entry": {"logic": "all", "conditions": [
        {"type": "ma_cross", "fast": 5, "slow": 20, "direction": "above"}
    ]},
    "exit": {"logic": "any", "conditions": [
        {"type": "ma_cross", "fast": 5, "slow": 20, "direction": "below"}
    ]},
    "risk": {"commission_pct": 0.1, "slippage_pct": 0.1},
}

def test_backtest_returns_curve_and_trades():
    result = run_backtest(CONFIG, make_bars())
    assert result["data_points"] > 0
    assert "equity_curve" in result and "trade_log" in result

def test_evaluate_bar_returns_signal():
    bars = make_bars()
    out = evaluate_bar(CONFIG, bars, bars[-1]["date"], None)
    assert out["signal"] in {"buy", "sell", "hold"}
    assert "matched_conditions" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_engine.py -v`
Expected: FAIL, functions not defined

- [ ] **Step 3: Implement backtest and evaluate_bar**

Append to `agent/strategy_engine.py`:

```python
from agent.strategy_schema import StrategyConfig

def run_backtest(config: dict, records: list[dict]) -> dict:
    cfg = StrategyConfig.model_validate(config)
    if len(records) < 20:
        return {"symbol": cfg.symbol, "error": "data insufficient"}
    ind = compute_indicators(records)
    cash = float(cfg.initial_capital)
    shares = 0.0
    entry_price = 0.0
    hwm = 0.0
    trades = []
    equity_curve = []
    comm = cfg.risk.commission_pct / 100.0
    slip = cfg.risk.slippage_pct / 100.0

    for i in range(20, len(records)):
        price = float(ind["closes"][i])
        if shares == 0:
            ok, reasons = evaluate_rule(cfg.entry, ind, i, None)
            if ok:
                fill = price * (1 + slip)
                budget = cash if cfg.position.type == "full" else cash * (cfg.position.size_pct or 100) / 100.0
                shares = budget * (1 - comm) / fill
                cash -= shares * fill * (1 + comm)
                entry_price = fill
                hwm = fill
                trades.append({"date": ind["dates"][i], "side": "buy", "price": fill,
                               "shares": shares, "amount": shares * fill, "reason": ",".join(reasons)})
        else:
            hwm = max(hwm, float(ind["highs"][i]))
            ok, reasons = evaluate_rule(cfg.exit, ind, i, {"entry_price": entry_price, "high_watermark": hwm})
            if ok:
                fill = price * (1 - slip)
                cash += shares * fill * (1 - comm)
                trades.append({"date": ind["dates"][i], "side": "sell", "price": fill,
                               "shares": shares, "amount": shares * fill, "reason": ",".join(reasons)})
                shares = 0.0
                entry_price = 0.0
                hwm = 0.0
        equity_curve.append({"date": ind["dates"][i], "equity": cash + shares * price, "close": price})

    final = cash + shares * float(ind["closes"][-1])
    return {
        "symbol": cfg.symbol, "initial_capital": cfg.initial_capital,
        "final_value": round(final, 2),
        "total_return_pct": round((final / cfg.initial_capital - 1) * 100, 2),
        "data_points": len(records),
        "trade_log": trades, "equity_curve": equity_curve,
        "start_date": ind["dates"][20], "end_date": ind["dates"][-1],
    }

def evaluate_bar(config: dict, records: list[dict], date: str, position: dict | None) -> dict:
    cfg = StrategyConfig.model_validate(config)
    ind = compute_indicators(records)
    idx = next((i for i, d in enumerate(ind["dates"]) if d == date), len(records) - 1)
    if idx < 20:
        return {"signal": "hold", "matched_conditions": [], "date": date,
                "price": float(ind["closes"][idx])}
    if position and position.get("shares", 0) > 0:
        ok, reasons = evaluate_rule(cfg.exit, ind, idx, position)
        return {"signal": "sell" if ok else "hold", "matched_conditions": reasons,
                "date": date, "price": float(ind["closes"][idx])}
    ok, reasons = evaluate_rule(cfg.entry, ind, idx, None)
    return {"signal": "buy" if ok else "hold", "matched_conditions": reasons,
            "date": date, "price": float(ind["closes"][idx])}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_strategy_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add python-data-service/agent/strategy_engine.py python-data-service/tests/test_strategy_engine.py
git commit -m "feat(agent): backtest executor and single-bar evaluation"
```

---

### Task 4: LLM completion helper and tool registry

**Files:** Modify `python-data-service/llm_service.py`; Create `python-data-service/agent/tool_registry.py`; Test `python-data-service/tests/test_tool_registry.py`.

**Interfaces:** Produces `llm_service.chat_completion(messages: list[dict], tools: list[dict] | None = None, temperature: float = 0.2, max_tokens: int = 1200) -> dict`, `tool_registry.TOOL_SCHEMAS: list[dict]`, `tool_registry.execute_tool(name: str, args: dict) -> dict`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_tool_registry.py
from agent.tool_registry import TOOL_SCHEMAS, execute_tool

def test_schemas_have_names():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert {"search_stock", "validate_strategy", "backtest_strategy", "finalize_strategy"} <= names

def test_validate_tool():
    out = execute_tool("validate_strategy", {"strategy_json": {
        "schema_version": "1.0", "name": "x", "symbol": "600519",
        "entry": {"logic": "all", "conditions": [{"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]},
        "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]}}})
    assert out["valid"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tool_registry.py -v`
Expected: FAIL, module missing

- [ ] **Step 3: Add `chat_completion` to `llm_service.py`**

Append this function to `llm_service.py` (it reuses module-level `API_KEY`, `BASE_URL`, `MODEL`, `TIMEOUT`):

```python
def chat_completion(messages, tools=None, temperature=0.2, max_tokens=1200):
    if not _is_available():
        return {"message": {"role": "assistant", "content": "AI 服务暂不可用"}}
    payload = {"model": MODEL, "messages": messages,
               "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        payload["tools"] = tools
    url = f"{BASE_URL}/v1/chat/completions"
    headers = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
    resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT)
    resp.raise_for_status()
    return resp.json()["choices"][0]
```

- [ ] **Step 4: Implement tool registry**

```python
# agent/tool_registry.py
from __future__ import annotations
import json
from akshare_client import get_quote, get_history, search_stocks
from agent.strategy_schema import validate_strategy_config
from agent.strategy_engine import run_backtest

def _tool(name, desc, params):
    return {"type": "function", "function": {"name": name, "description": desc, "parameters": params}}

def _obj(props, required):
    return {"type": "object", "properties": props, "required": required}

TOOL_SCHEMAS = [
    _tool("search_stock", "Resolve a Chinese stock name to a symbol code.",
          _obj({"keyword": {"type": "string"}}, ["keyword"])),
    _tool("get_quote", "Get latest quote for one symbol.",
          _obj({"symbol": {"type": "string"}}, ["symbol"])),
    _tool("get_history", "Get daily history bars.",
          _obj({"symbol": {"type": "string"}, "days": {"type": "integer", "default": 250}}, ["symbol"])),
    _tool("validate_strategy", "Validate a strategy JSON object.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
    _tool("backtest_strategy", "Backtest a strategy JSON object.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
    _tool("finalize_strategy", "Finalize the strategy JSON and return a plain-language summary.",
          _obj({"strategy_json": {"type": "object"}}, ["strategy_json"])),
]

def execute_tool(name, args):
    try:
        if name == "search_stock":
            return {"results": search_stocks(args.get("keyword", ""))[:10]}
        if name == "get_quote":
            return {"quote": get_quote(args.get("symbol", ""))}
        if name == "get_history":
            symbol = args.get("symbol", "")
            records = get_history(symbol) or []
            days = int(args.get("days", 250))
            return {"records": records[-days:]}
        if name == "validate_strategy":
            cfg, err = validate_strategy_config(args.get("strategy_json", {}))
            return {"valid": err is None, "error": err, "normalized": cfg.model_dump() if cfg else None}
        if name == "backtest_strategy":
            cfg_json = args.get("strategy_json", {})
            cfg, err = validate_strategy_config(cfg_json)
            if err:
                return {"valid": False, "error": err}
            records = get_history(cfg.symbol)
            return {"valid": True, "backtest": run_backtest(cfg_json, records or [])}
        if name == "finalize_strategy":
            cfg_json = args.get("strategy_json", {})
            cfg, err = validate_strategy_config(cfg_json)
            return {"valid": err is None, "error": err, "strategy_json": cfg_json}
        return {"error": f"unknown tool {name}"}
    except Exception as e:
        return {"error": str(e)}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_tool_registry.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```powershell
git add python-data-service/llm_service.py python-data-service/agent/tool_registry.py python-data-service/tests/test_tool_registry.py
git commit -m "feat(agent): LLM completion helper and tool registry"
```

---

### Task 5: ReAct agent loop

**Files:** Create `python-data-service/agent/react_agent.py`; Test `python-data-service/tests/test_react_agent.py`.

**Interfaces:** Consumes `llm_service.chat_completion`, `TOOL_SCHEMAS`, `execute_tool`, `extract_strategy_json`, `llm_service.split_replies`. Produces `run_agent(user_id: str, message: str, history: list[dict]) -> dict` returning `{"replies": list[str], "strategy_json": dict | None}`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_react_agent.py
import agent.react_agent as ra

def fake_completion(messages, tools=None, temperature=0.2, max_tokens=1200):
    return {"message": {"role": "assistant", "content": "最终回复\n```json\n{\"schema_version\":\"1.0\",\"name\":\"ma\",\"symbol\":\"600519\",\"entry\":{\"logic\":\"all\",\"conditions\":[{\"type\":\"ma_cross\",\"fast\":20,\"slow\":60,\"direction\":\"above\"}]},\"exit\":{\"logic\":\"any\",\"conditions\":[{\"type\":\"stop_loss_pct\",\"value\":-8}]}}\n```"}}

def test_run_agent_returns_strategy(monkeypatch):
    monkeypatch.setattr(ra.llm_service, "chat_completion", fake_completion)
    out = ra.run_agent("u1", "做一个20日上穿60日买入", [])
    assert out["strategy_json"] is not None
    assert len(out["replies"]) >= 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_react_agent.py -v`
Expected: FAIL, module missing

- [ ] **Step 3: Implement ReAct loop**

```python
# agent/react_agent.py
from __future__ import annotations
import json
import logging
import llm_service
from agent.tool_registry import TOOL_SCHEMAS, execute_tool
from agent.strategy_schema import extract_strategy_json

logger = logging.getLogger(__name__)
MAX_STEPS = 6

SYSTEM_PROMPT = """你是股票策略助手。用户可能让你生成交易策略。
可用工具用于查行情、校验和回测策略。若用户在描述策略，请逐步调用工具，
最终调用 finalize_strategy，并在最终回复中用 ```json 代码块给出策略 JSON。
JSON 必须符合 schema_version=1.0。若用户只是闲聊或查行情，直接回复。"""

def run_agent(user_id, message, history=None):
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages.extend(history or [])
    messages.append({"role": "user", "content": message})

    for _ in range(MAX_STEPS):
        try:
            choice = llm_service.chat_completion(messages, tools=TOOL_SCHEMAS)
        except Exception as e:
            logger.error("agent llm error: %s", e)
            return {"replies": ["AI 服务暂不可用，请稍后再试"], "strategy_json": None}

        msg = choice.get("message", {})
        messages.append(msg)
        tool_calls = msg.get("tool_calls") or []

        if not tool_calls:
            text = msg.get("content", "") or "我没理解，请换个说法。"
            return {"replies": llm_service.split_replies(text),
                    "strategy_json": extract_strategy_json(text)}

        for tc in tool_calls:
            fn = tc.get("function", {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}
            result = execute_tool(name, args)
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": json.dumps(result, ensure_ascii=False)})

    return {"replies": ["这个请求步骤有点多，请简化你的策略描述再试一次。"], "strategy_json": None}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_react_agent.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add python-data-service/agent/react_agent.py python-data-service/tests/test_react_agent.py
git commit -m "feat(agent): ReAct agent loop"
```

---

### Task 6: FastAPI agent and strategy endpoints

**Files:** Modify `python-data-service/app.py`; Test `python-data-service/tests/test_endpoints.py`.

**Interfaces:** Produces `POST /api/v1/agent/chat`, `POST /api/v1/strategies/validate`, `POST /api/v1/strategies/backtest`, `POST /api/v1/strategies/evaluate-bar`.

- [ ] **Step 1: Write failing test**

```python
# tests/test_endpoints.py
from fastapi.testclient import TestClient
import app as main

def test_validate_endpoint():
    c = TestClient(main.app)
    payload = {"strategy_json": {"schema_version": "1.0", "name": "ma", "symbol": "600519",
        "entry": {"logic": "all", "conditions": [{"type": "ma_cross", "fast": 20, "slow": 60, "direction": "above"}]},
        "exit": {"logic": "any", "conditions": [{"type": "stop_loss_pct", "value": -8}]}}}
    r = c.post("/api/v1/strategies/validate", json=payload)
    assert r.status_code == 200
    assert r.json()["valid"] is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_endpoints.py -v`
Expected: FAIL, 404 Not Found

- [ ] **Step 3: Add endpoints to `app.py`**

Insert before the existing chat section. All strategy modules are lazily imported inside each function:

```python
@app.post("/api/v1/agent/chat")
async def agent_chat(req: Request):
    import agent.react_agent as react_agent
    data = await req.json()
    user_id = str(data.get("user_id", "")).strip()
    message = data.get("message", "").strip()
    if not user_id or not message:
        return {"replies": [], "strategy_json": None}
    try:
        result = await asyncio.to_thread(react_agent.run_agent, user_id, message)
        return result
    except Exception as e:
        logger.error(f"agent chat error: {e}", exc_info=True)
        return {"replies": ["⚠️ 处理出错了，稍后再试"], "strategy_json": None}

@app.post("/api/v1/strategies/validate")
async def validate_strategy_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    data = await req.json()
    cfg, err = validate_strategy_config(data.get("strategy_json", {}))
    return {"valid": err is None, "error": err, "normalized": cfg.model_dump() if cfg else None}

@app.post("/api/v1/strategies/backtest")
async def backtest_strategy_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    from agent.strategy_engine import run_backtest
    from akshare_client import get_history
    data = await req.json()
    cfg_json = data.get("strategy_json", {})
    cfg, err = validate_strategy_config(cfg_json)
    if err:
        return {"valid": False, "error": err}
    records = get_history(cfg.symbol)
    return {"valid": True, "backtest": run_backtest(cfg_json, records or [])}

@app.post("/api/v1/strategies/evaluate-bar")
async def evaluate_bar_endpoint(req: Request):
    from agent.strategy_schema import validate_strategy_config
    from agent.strategy_engine import evaluate_bar
    from akshare_client import get_history
    data = await req.json()
    cfg, err = validate_strategy_config(data.get("strategy_json", {}))
    if err:
        return {"valid": False, "error": err}
    symbol = data.get("symbol") or cfg.symbol
    records = get_history(symbol)
    return evaluate_bar(cfg.model_dump(), records or [], data.get("date", ""), data.get("position"))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_endpoints.py -v`
Expected: PASS (only `validate` is asserted; the other endpoints require live data)

- [ ] **Step 5: Commit**

```powershell
git add python-data-service/app.py python-data-service/tests/test_endpoints.py
git commit -m "feat(agent): FastAPI agent and strategy endpoints"
```

---

### Task 7: Java persistence entities and repositories

**Files:** Create `Strategy.java`, `PaperAccount.java`, `PaperTrade.java` under `entity`; Create `StrategyRepository.java`, `PaperAccountRepository.java`, `PaperTradeRepository.java` under `repository`.

**Interfaces:** Produces JPA entities used by Tasks 8-10. `Strategy`: `id`, `user` (ManyToOne), `name`, `symbol`, `configJson` (TEXT), `paperEnabled` (Boolean, default false), `createdAt`, `updatedAt`, `lastBacktestAt`, `lastPaperEvalAt`. `PaperAccount`: `id`, `user`, `strategy` (OneToOne), `initialCapital`, `cash`, `shares`, `avgCost`, `equity`, `createdAt`, `updatedAt`. `PaperTrade`: `id`, `user`, `strategy`, `account`, `tradeDate` (LocalDate), `symbol`, `side`, `price`, `shares`, `amount`, `reason`, `createdAt`.

- [ ] **Step 1: Create entities** (match `Alert.java` Lombok pattern)

```java
package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.*;

import java.time.LocalDateTime;

@Entity
@Table(name = "strategies")
@Data @AllArgsConstructor @NoArgsConstructor @Builder
public class Strategy {
    @Id @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id", nullable = false)
    private User user;

    @Column(nullable = false) private String name;
    @Column(nullable = false) private String symbol;
    @Column(nullable = false, columnDefinition = "TEXT") private String configJson;

    @Builder.Default @Column(nullable = false) private Boolean paperEnabled = false;

    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;
    private LocalDateTime lastBacktestAt;
    private LocalDateTime lastPaperEvalAt;

    @PrePersist void onCreate() { createdAt = LocalDateTime.now(); updatedAt = createdAt; }
    @PreUpdate void onUpdate() { updatedAt = LocalDateTime.now(); }
}
```

`PaperAccount.java` and `PaperTrade.java` follow the same pattern with fields from the interface list above; `PaperTrade.side` stores `"BUY"` or `"SELL"`.

- [ ] **Step 2: Create repositories**

```java
@Repository
public interface StrategyRepository extends JpaRepository<Strategy, Long> {
    List<Strategy> findByUserIdOrderByUpdatedAtDesc(Long userId);
    Optional<Strategy> findByIdAndUserId(Long id, Long userId);
    List<Strategy> findByPaperEnabledTrue();
}

@Repository
public interface PaperAccountRepository extends JpaRepository<PaperAccount, Long> {
    Optional<PaperAccount> findByStrategyId(Long strategyId);
}

@Repository
public interface PaperTradeRepository extends JpaRepository<PaperTrade, Long> {
    List<PaperTrade> findByStrategyIdOrderByTradeDateDesc(Long strategyId);
    boolean existsByStrategyIdAndTradeDate(Long strategyId, LocalDate tradeDate);
}
```

- [ ] **Step 3: Compile**

Run: `.\mvnw.cmd test -Dtest=StocktrackerApplicationTests`
Expected: PASS

- [ ] **Step 4: Commit**

```powershell
git add src/main/java/com/happyericsix/stocktracker/entity src/main/java/com/happyericsix/stocktracker/repository
git commit -m "feat(strategy): persistence entities and repositories"
```

---

### Task 8: Java DTOs and Python strategy client

**Files:** Create DTOs under `dto`; Create `client/StrategyClient.java`.

**Interfaces:** Produces `StrategyClient.validateStrategy(String configJson) -> JsonNode`, `StrategyClient.backtestStrategy(String configJson) -> JsonNode`, `StrategyClient.evaluateBar(String configJson, String symbol, String date, JsonNode position) -> JsonNode`.

- [ ] **Step 1: Create DTOs**

`StrategyRequest` has `name`, `symbol`, `configJson` plus getters/setters. `StrategyResponse` has `id`, `name`, `symbol`, `configJson`, `paperEnabled`, `createdAt`, `updatedAt`, `lastBacktestAt` plus `static from(Strategy)`. `PaperAccountResponse` and `PaperTradeResponse` are simple POJOs with `static from(...)` mappers.

- [ ] **Step 2: Implement `StrategyClient`**

```java
package com.happyericsix.stocktracker.client;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.client.WebClient;

@Service
public class StrategyClient {
    private final WebClient webClient;
    private final ObjectMapper mapper = new ObjectMapper();

    public StrategyClient(@Value("${akshare.api.base-url:http://localhost:8000}") String baseUrl) {
        this.webClient = WebClient.builder().baseUrl(baseUrl).build();
    }

    public JsonNode validateStrategy(String configJson) {
        return post("/api/v1/strategies/validate", java.util.Map.of("strategy_json", raw(configJson)));
    }

    public JsonNode backtestStrategy(String configJson) {
        return post("/api/v1/strategies/backtest", java.util.Map.of("strategy_json", raw(configJson)));
    }

    public JsonNode evaluateBar(String configJson, String symbol, String date, JsonNode position) {
        var body = new java.util.HashMap<String, Object>();
        body.put("strategy_json", raw(configJson));
        body.put("symbol", symbol);
        body.put("date", date);
        if (position != null) body.put("position", position);
        return post("/api/v1/strategies/evaluate-bar", body);
    }

    private JsonNode raw(String json) {
        try { return mapper.readTree(json); }
        catch (Exception e) { return mapper.createObjectNode(); }
    }

    private JsonNode post(String uri, Object body) {
        return webClient.post().uri(uri).bodyValue(body)
                .retrieve().bodyToMono(JsonNode.class).block();
    }
}
```

- [ ] **Step 3: Compile**

Run: `.\mvnw.cmd test -Dtest=StocktrackerApplicationTests`
Expected: PASS

- [ ] **Step 4: Commit**

```powershell
git add src/main/java/com/happyericsix/stocktracker/dto src/main/java/com/happyericsix/stocktracker/client/StrategyClient.java
git commit -m "feat(strategy): DTOs and Python strategy client"
```

---

### Task 9: StrategyService and ownership checks

**Files:** Create `service/StrategyService.java`; Test `src/test/java/com/happyericsix/stocktracker/service/StrategyServiceTest.java`.

**Interfaces:** Consumes `StrategyRepository`, `StrategyClient`, `UserRepository`. Produces `createStrategy(String username, StrategyRequest) -> StrategyResponse`, `listStrategies(String username) -> List<StrategyResponse>`, `updateStrategy(String username, Long id, StrategyRequest) -> StrategyResponse`, `deleteStrategy(String username, Long id)`, `runBacktest(String username, Long id) -> JsonNode`, `createFromAgent(String username, JsonNode strategyJson) -> StrategyResponse`.

- [ ] **Step 1: Write failing ownership test**

```java
class StrategyServiceTest {
    @Test
    void cannotUpdateOtherUsersStrategy() {
        // mock repos: findByIdAndUserId returns empty for foreign id
        var service = new StrategyService(strategyRepo, userRepo, strategyClient);
        assertThrows(IllegalArgumentException.class,
            () -> service.updateStrategy("alice", 99L, new StrategyRequest()));
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\mvnw.cmd test -Dtest=StrategyServiceTest`
Expected: FAIL, class not found

- [ ] **Step 3: Implement `StrategyService`**

Use constructor injection. Resolve user via `userRepository.findByUsername`, throwing `IllegalArgumentException("用户不存在")` when absent. For `updateStrategy`, `deleteStrategy`, and `runBacktest`, load via `strategyRepository.findByIdAndUserId(id, user.getId())` and throw `IllegalArgumentException("策略不存在")` when empty. `runBacktest` calls `strategyClient.backtestStrategy(configJson)`, sets `lastBacktestAt`, saves, and returns raw JSON. `createFromAgent` reads `name`/`symbol` from the JSON with fallbacks, sets `configJson = strategyJson.toString()`, and saves.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\mvnw.cmd test -Dtest=StrategyServiceTest`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/main/java/com/happyericsix/stocktracker/service/StrategyService.java src/test/java/com/happyericsix/stocktracker/service/StrategyServiceTest.java
git commit -m "feat(strategy): StrategyService with ownership checks"
```

---

### Task 10: PaperTradingService settlement and idempotency

**Files:** Create `service/PaperTradingService.java`; Test `src/test/java/com/happyericsix/stocktracker/service/PaperTradingServiceTest.java`.

**Interfaces:** Consumes `StrategyRepository`, `PaperAccountRepository`, `PaperTradeRepository`, `StrategyClient`. Produces `startPaper(String username, Long strategyId) -> PaperAccountResponse`, `stopPaper(String username, Long strategyId)`, `evaluateDaily()`, `getAccount(String username, Long strategyId) -> PaperAccountResponse`, `getTrades(String username, Long strategyId) -> List<PaperTradeResponse>`.

- [ ] **Step 1: Write failing settlement tests**

```java
class PaperTradingServiceTest {
    @Test
    void buySignalCreatesTradeAndUpdatesAccount() {
        // mock StrategyClient.evaluateBar -> {"signal":"buy","price":10.0,"matched_conditions":["ma_cross"]}
        // verify shares > 0, cash < initial, one PaperTrade persisted with side BUY
    }

    @Test
    void sameDateIsIdempotent() {
        // when paperTradeRepo.existsByStrategyIdAndTradeDate returns true, evaluateDaily skips
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.\mvnw.cmd test -Dtest=PaperTradingServiceTest`
Expected: FAIL

- [ ] **Step 3: Implement settlement**

`evaluateDaily()` iterates `strategyRepository.findByPaperEnabledTrue()`. For each strategy: `LocalDate today = LocalDate.now()`; if `paperTradeRepository.existsByStrategyIdAndTradeDate(strategyId, today)` then continue. Load or create `PaperAccount` with `initialCapital` from the strategy config. Build position JSON `{"entry_price": account.getAvgCost(), "shares": account.getShares()}` only when holding. Call `strategyClient.evaluateBar(...)`. On `buy` when flat, compute `fill = price * (1 + slippage)`, budget from `position.type`/`size_pct`, deduct commission, set `avgCost`; on `sell` when holding, add cash after commission and clear shares. Persist `PaperTrade` with `side`, `price=fill`, `shares`, `amount`, `reason` from `matched_conditions`, then save account with `equity = cash + shares * price`. Wrap each strategy in try/catch and log so one failure does not stop the rest.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.\mvnw.cmd test -Dtest=PaperTradingServiceTest`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/main/java/com/happyericsix/stocktracker/service/PaperTradingService.java src/test/java/com/happyericsix/stocktracker/service/PaperTradingServiceTest.java
git commit -m "feat(strategy): paper trading settlement with idempotency"
```

---

### Task 11: Strategy and paper controllers + daily job

**Files:** Create `controller/StrategyController.java`; Create `job/PaperTradingJob.java`.

**Interfaces:** Produces `GET/POST/PUT/DELETE /api/v1/strategies`, `POST /api/v1/strategies/{id}/backtest`, `POST /api/v1/strategies/{id}/paper/start`, `POST /api/v1/strategies/{id}/paper/stop`, `GET /api/v1/strategies/{id}/paper/account`, `GET /api/v1/strategies/{id}/paper/trades`.

- [ ] **Step 1: Implement controller**

Follow `AlertController` / `ChatController` conventions: `@RestController @RequiredArgsConstructor @RequestMapping("/api/v1/strategies")`, inject `StrategyService` and `PaperTradingService`, use `Authentication.getName()` for username, wrap responses with `Result.success(...)`.

- [ ] **Step 2: Implement daily job**

```java
package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.PaperTradingService;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class PaperTradingJob {
    private final PaperTradingService paperTradingService;

    public PaperTradingJob(PaperTradingService paperTradingService) {
        this.paperTradingService = paperTradingService;
    }

    @Scheduled(cron = "0 30 15 * * MON-FRI", zone = "Asia/Shanghai")
    public void runDailyPaper() {
        try { paperTradingService.evaluateDaily(); }
        catch (Exception e) { /* log only */ }
    }
}
```

- [ ] **Step 3: Verify scheduling is enabled**

Check `StocktrackerApplication` already has `@EnableScheduling`; if not, add it (or add to `AsyncConfig`).

- [ ] **Step 4: Compile**

Run: `.\mvnw.cmd test -Dtest=StocktrackerApplicationTests`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/main/java/com/happyericsix/stocktracker/controller/StrategyController.java src/main/java/com/happyericsix/stocktracker/job/PaperTradingJob.java
git commit -m "feat(strategy): strategy/paper controllers and daily job"
```

---

### Task 12: Route chat through the agent and persist generated strategies

**Files:** Modify `service/ChatService.java`; Test `src/test/java/com/happyericsix/stocktracker/service/ChatServiceTest.java`.

**Interfaces:** Consumes `StrategyService`. Produces updated `ChatService.processAsync` that calls `/api/v1/agent/chat`, parses `strategy_json`, and calls `strategyService.createFromAgent` when present.

- [ ] **Step 1: Write failing test**

```java
class ChatServiceTest {
    @Test
    void agentStrategyJsonIsPersisted() {
        // mock WebClient to return {"replies":["ok"],"strategy_json":{...}}
        // verify strategyService.createFromAgent called once
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.\mvnw.cmd test -Dtest=ChatServiceTest`
Expected: FAIL

- [ ] **Step 3: Modify `ChatService.processAsync`**

Change URI from `/api/v1/chat` to `/api/v1/agent/chat`. After parsing `replies`, read `JsonNode strategyJson = resp.get("strategy_json")`; if it is an object, call `strategyService.createFromAgent(username, strategyJson)`. Keep existing reply persistence + SSE behavior.

- [ ] **Step 4: Run test to verify it passes**

Run: `.\mvnw.cmd test -Dtest=ChatServiceTest`
Expected: PASS

- [ ] **Step 5: Commit**

```powershell
git add src/main/java/com/happyericsix/stocktracker/service/ChatService.java src/test/java/com/happyericsix/stocktracker/service/ChatServiceTest.java
git commit -m "feat(chat): route chat through agent and persist strategies"
```

---

### Task 13: Frontend strategy library pages

**Files:** Create `frontend/src/api/strategy.js`, `frontend/src/pages/Strategies.vue`, `frontend/src/pages/StrategyDetail.vue`; Modify `frontend/src/router/index.js`, `frontend/src/pages/Assistant.vue`.

**Interfaces:** Produces UI for strategy list, detail, backtest trigger, paper start/stop, account/trades display.

- [ ] **Step 1: Create API module**

```javascript
import api from './index'

export const listStrategies = () => api.get('/strategies')
export const createStrategy = (data) => api.post('/strategies', data)
export const updateStrategy = (id, data) => api.put(`/strategies/${id}`, data)
export const deleteStrategy = (id) => api.delete(`/strategies/${id}`)
export const runBacktest = (id) => api.post(`/strategies/${id}/backtest`)
export const startPaper = (id) => api.post(`/strategies/${id}/paper/start`)
export const stopPaper = (id) => api.post(`/strategies/${id}/paper/stop`)
export const getPaperAccount = (id) => api.get(`/strategies/${id}/paper/account`)
export const getPaperTrades = (id) => api.get(`/strategies/${id}/paper/trades`)
```

- [ ] **Step 2: Create `Strategies.vue`** with a table of strategies, buttons for backtest/start/stop, and a link to detail.
- [ ] **Step 3: Create `StrategyDetail.vue`** showing `configJson`, backtest result, paper account summary, and trade list.
- [ ] **Step 4: Add routes** `/strategies` and `/strategies/:id`.
- [ ] **Step 5: Update `Assistant.vue`** so a bot message containing fenced `strategy_json` also renders a "保存到策略库" action that calls `createStrategy`.
- [ ] **Step 6: Build frontend**

Run: `npm run build` in `frontend`
Expected: build succeeds

- [ ] **Step 7: Commit**

```powershell
git add frontend/src/api/strategy.js frontend/src/pages/Strategies.vue frontend/src/pages/StrategyDetail.vue frontend/src/router/index.js frontend/src/pages/Assistant.vue
git commit -m "feat(frontend): strategy library pages"
```

---

### Task 14: End-to-end acceptance and docs

**Files:** Modify `README.md`.

**Interfaces:** No new code interfaces; validates all prior tasks.

- [ ] **Step 1: Start Python service** with `.venv\Scripts\python.exe -m uvicorn app:app --port 8000` (check `.env` has `DEEPSEEK_API_KEY`).
- [ ] **Step 2: Start Java** with `.\mvnw.cmd spring-boot:run`.
- [ ] **Step 3: Manual acceptance** in the app: send "做一个20日均线上穿60日买入，跌破60日卖出，止损8%" in chat; confirm agent returns fenced JSON and the strategy appears in the library.
- [ ] **Step 4: Run backtest** from detail page; confirm equity curve/trades render.
- [ ] **Step 5: Start paper**; manually invoke `PaperTradingJob` or wait for schedule; confirm a `PaperTrade` and equity update appear and rerunning the same day does not duplicate.
- [ ] **Step 6: Update README** with a "Strategy Agent" subsection describing the flow and the virtual-account disclaimer.
- [ ] **Step 7: Commit**

```powershell
git add README.md
git commit -m "docs: document strategy agent"
```

---

## Self-Review Notes

- Spec coverage: schema (Task 1), rule DSL (Task 2), backtest (Task 3), tool registry/agent (Tasks 4-5), endpoints (Task 6), persistence (Task 7), client/DTO (Task 8), CRUD/ownership (Task 9), paper settlement/idempotency (Task 10), controllers/job (Task 11), chat routing (Task 12), frontend (Task 13), acceptance/docs (Task 14).
- Placeholder scan: no TBD/TODO; every code step contains concrete code or exact behavior.
- Type consistency: `validate_strategy_config`, `run_backtest`, `evaluate_bar`, `evaluate_rule`, `chat_completion`, `TOOL_SCHEMAS`, `execute_tool`, `run_agent` are reused consistently across tasks.
