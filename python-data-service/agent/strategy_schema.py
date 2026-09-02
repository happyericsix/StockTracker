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

