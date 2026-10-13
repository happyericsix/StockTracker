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
    # 2026-10-13 补的两条：委员会在自进化回放里**自己试图发明**这两个条件
    # （提案两次被 schema 拦下）——老手语境最常用、词表却没覆盖，这就是需求本身。
    #   bias_pct：乖离率过滤（防追高）——"上穿 MA60 且乖离 ≤5%"的写法从此存在；
    #   volume_ratio：量比（放量/缩量确认）——当日量 / 过去 N 日均量与阈值比较。
    "bias_pct", "volume_ratio",
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
        # 消息全部中文：这些错误会一路透传到策略编辑面板给最终用户看
        # （2026-10-10 用户反馈"怎么能直接把后端报错拿上来"）。
        if t not in CONDITION_TYPES:
            raise ValueError(f"不支持的条件类型：{t}")
        if t == "ma_cross":
            if self.fast is None or self.slow is None or self.direction is None:
                raise ValueError("均线交叉需要快线、慢线两个天数和方向（上穿/下穿）")
        elif t == "macd_cross":
            if self.direction is None:
                raise ValueError("MACD 交叉需要方向（上穿=金叉 / 下穿=死叉）")
        elif t == "price_cross_ma":
            if self.window is None or self.direction is None:
                raise ValueError("价格穿越均线需要均线天数和方向（上穿/下穿）")
        elif t in {"price_above_ma", "price_below_ma"}:
            if self.window is None:
                raise ValueError("价格与均线的比较需要均线天数")
        elif t in {"rsi_above", "rsi_below", "price_above", "price_below",
                   "stop_loss_pct", "take_profit_pct", "trailing_stop_pct"}:
            if self.value is None:
                raise ValueError("这一条件需要一个数值（阈值或百分比）")
        elif t == "bias_pct":
            if self.window is None or self.value is None:
                raise ValueError("乖离率过滤需要均线天数和阈值（如 5 = 偏离均线不超过 5%）")
            if self.value < 0:
                raise ValueError("bias_pct 的阈值必须为非负数（如 5 表示乖离不超过 5%）")
        elif t == "volume_ratio":
            if self.window is None or self.value is None or self.direction is None:
                raise ValueError("量比条件需要天数、倍数阈值和方向（above=放量 / below=缩量）")
            if self.value <= 0:
                raise ValueError("volume_ratio 的阈值必须为正数（如 1.2 表示 1.2 倍）")
        # 风控参数的符号约定（引擎按"收益率与 value 直接比较"实现，符号写反不是风格问题）：
        #   stop_loss_pct：收益率 ≤ value 触发 → value 必须为负（-8 = 亏 8% 止损）。
        #     写成 +8 会让条件恒成立（除非跌 8% 以上），入场即卖。
        #   take_profit_pct：收益率 ≥ value 触发 → value 必须为正（8 = 赚 8% 止盈）。
        #   trailing_stop_pct：从最高水位回撤 ≥ value 触发 → value 必须为正（8 = 回撤 8%）。
        if t == "stop_loss_pct" and self.value is not None and self.value >= 0:
            raise ValueError("stop_loss_pct 的 value 必须为负数（如 -8 表示亏 8% 止损）")
        if t == "take_profit_pct" and self.value is not None and self.value <= 0:
            raise ValueError("take_profit_pct 的 value 必须为正数（如 8 表示赚 8% 止盈）")
        if t == "trailing_stop_pct" and self.value is not None and self.value <= 0:
            raise ValueError("trailing_stop_pct 的 value 必须为正数（如 8 表示从最高点回撤 8% 止损）")
        if t in {"price_cross_ma", "price_above_ma", "price_below_ma",
                 "bias_pct", "volume_ratio"}:
            # 均线至少要有两个点才有意义；窗口为 1 时"价格与均线的关系"恒等于价格与价格
            if self.window < 2:
                raise ValueError("均线天数至少为 2（为 1 时均线就是价格本身，条件失去意义）")
        return self

class RuleGroup(BaseModel):
    logic: Literal["all", "any"] = "all"
    conditions: list[Condition] = Field(min_length=1)

class PositionRule(BaseModel):
    type: Literal["full", "percent"] = "full"
    size_pct: Optional[float] = 100.0

    @model_validator(mode="after")
    def _check(self):
        if self.type == "percent" and self.size_pct is not None and not (0 < self.size_pct <= 100):
            # 0 在 Python 侧曾因 `or 100` 被放大成全仓、在结算侧（Java）却是"预算为零买不进"
            # —— 两侧行为相反。它是配置错误，应该在入口就被拒绝，而不是被任何一侧悄悄解释。
            raise ValueError("position.size_pct 必须在 (0, 100] 之间（百分比仓位）")
        return self

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

# pydantic 框架消息 → 中文（自定义 ValueError 的消息本来就是中文，不走这张表）
_FRAMEWORK_MSG_ZH = {
    "Field required": "缺少必填字段",
    "List should have at least 1 item": "至少要有一条条件",
    "Input should be 'all' or 'any'": "组合方式只能是 全部满足(all) 或 任一满足(any)",
    "Input should be 'day'": "K 线周期只支持日线(day)",
    "Input should be 'full' or 'percent'": "仓位类型只支持 全仓(full) 或 百分比(percent)",
    "Input should be '1.0'": "schema_version 只支持 1.0",
    "Input should be a valid number": "这里应该是一个数字",
    "Input should be a valid string": "这里应该是文字",
    "Input should be a valid integer": "这里应该是一个整数",
}

# 字段路径 → 人话（错误要能定位到"哪一块"，但不能拿英文路径考验用户）
_LOC_ZH = {
    "entry": "入场条件", "exit": "出场条件", "conditions": "条件",
    "position": "仓位", "size_pct": "仓位百分比", "risk": "费用假设",
    "name": "策略名称", "symbol": "标的代码", "initial_capital": "初始资金",
    "data": "数据设置", "logic": "组合方式",
}

def _humanize_loc(loc: tuple) -> str:
    parts = []
    for item in loc:
        text = str(item)
        parts.append(_LOC_ZH.get(text, text if text.isdigit() else f"第{text}项" if False else text))
    # 纯数字段（条件下标）换成"第 N 条"
    parts = [f"第{int(p) + 1}条" if p.isdigit() else p for p in parts]
    return "/".join(parts)

def _humanize_validation_error(e: ValidationError) -> str:
    """把 pydantic 的报错翻成用户能直接看懂的一两句话（最多列 3 条）。

    错误会透传到策略编辑面板与 AI 聊天链路 —— 英文原文对两类受众都是噪音：
    用户看不懂，agent 还得转译一遍。
    """
    lines = []
    for err in e.errors()[:3]:
        raw = err.get("msg", "格式不正确")
        # 自定义校验器的消息会被 pydantic 冠上 "Value error, " 前缀 —— 那是给开发者
        # 看的类型标记，去掉后才是我们写的那句人话。
        if raw.startswith("Value error,"):
            raw = raw[len("Value error,"):].strip()
        msg = _FRAMEWORK_MSG_ZH.get(raw, raw)
        lines.append(f"{_humanize_loc(err.get('loc', ()))}：{msg}")
    if len(e.errors()) > 3:
        lines.append(f"……还有 {len(e.errors()) - 3} 处问题")
    return "；".join(lines)

def validate_strategy_config(data: dict):
    try:
        return StrategyConfig.model_validate(data), None
    except ValidationError as e:
        return None, _humanize_validation_error(e)

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

