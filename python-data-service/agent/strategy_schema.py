from __future__ import annotations
import json, re
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator, ValidationError

CONDITION_TYPES = {
    "ma_cross", "rsi_above", "rsi_below", "macd_cross",
    "price_above", "price_below",
    # 价格与均线的关系（补于 2026-09-17）：在此之前"跌破 60 日线卖出"这类说法
    # 在 DSL 里**没有等价写法** —— `price_above/below` 吃的是绝对价位，
    # `ma_cross` 吃的是两条均线的交叉，于是模型只能用"20 日均线下穿 60 日"近似糊过去，
    # 而这与用户说的不是同一件事（策略审查把它判成 requirement_mismatch 才发现）。
    "price_cross_ma", "price_above_ma", "price_below_ma",
    "stop_loss_pct", "take_profit_pct", "trailing_stop_pct",
}

class Condition(BaseModel):
    type: str
    fast: Optional[int] = None
    slow: Optional[int] = None
    direction: Optional[Literal["above", "below"]] = None
    value: Optional[float] = None
    # 价格与哪条均线比（price_cross_ma / price_above_ma / price_below_ma 用）
    window: Optional[int] = None

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
        elif t == "price_cross_ma":
            if self.window is None or self.direction is None:
                raise ValueError("price_cross_ma requires window, direction")
        elif t in {"price_above_ma", "price_below_ma"}:
            if self.window is None:
                raise ValueError(f"{t} requires window")
        elif t in {"rsi_above", "rsi_below", "price_above", "price_below",
                   "stop_loss_pct", "take_profit_pct", "trailing_stop_pct"}:
            if self.value is None:
                raise ValueError(f"{t} requires value")
        if t in {"price_cross_ma", "price_above_ma", "price_below_ma"}:
            # 均线至少要有两个点才有意义；窗口为 1 时"价格与均线的关系"恒等于价格与价格
            if self.window < 2:
                raise ValueError(f"{t} window must be >= 2")
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

