"""工具声明模型（T0）：一个工具"能做什么"和"谁能调、何时能调、失败怎么办"写在一起。

<h3>为什么要有这个文件</h3>
改造前，工具就是"一个函数 + 一条 schema"，策略（授权、预算、审批、重试、缓存）
散落在各处或者根本不存在：`_HANDLERS` 里只有 name→函数，谁能调、要不要确认、
失败了能不能重试，没人管。工具一多就只能靠约定，约定一多就必然漏。

这里把策略提升为**声明的一部分**：

    ToolSpec = 契约（schema/版本）+ 分类（层/副作用/成本）+ 策略（权限/审批/重试/缓存）

于是：
- 新增工具的默认行为是"安全的"（不声明就不能重试、有副作用就必须审批）；
- 执行管线可以**统一**做这些事，而不是每个 handler 各自记得；
- `validate_registry()` 能在启动时把自相矛盾的声明挑出来（例如"有副作用却标成可重试"）。

<h3>分层不按业务，按副作用与失败模式</h3>
重试、缓存、审批这些策略**只跟层有关**，不跟业务有关。按业务分（行情/策略/记忆）
会导致每加一个工具都要重新讨论一遍"这个能不能重试"；按层分则选层即继承策略。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

# ==================== 分类 ====================

LAYER_PURE = "L1"        # 纯计算/规则：确定性、无 IO、可缓存、可重试
LAYER_INTEGRATION = "L2"  # 集成/读取：有网络、会超时、结果会变、内容不可信
LAYER_ACTION = "L3"       # 动作/副作用：改变系统或外部状态 → 审批 + 幂等 + 审计
LAYERS = (LAYER_PURE, LAYER_INTEGRATION, LAYER_ACTION)

SIDE_EFFECT_NONE = "none"
SIDE_EFFECT_WRITE = "write"
SIDE_EFFECT_IRREVERSIBLE = "irreversible"
SIDE_EFFECTS = (SIDE_EFFECT_NONE, SIDE_EFFECT_WRITE, SIDE_EFFECT_IRREVERSIBLE)

APPROVAL_NONE = "none"
APPROVAL_CONFIRM = "confirm"   # 需要用户/前端确认
APPROVAL_DUAL = "dual"         # 需要两方确认（预留给下单这类不可逆动作）
APPROVALS = (APPROVAL_NONE, APPROVAL_CONFIRM, APPROVAL_DUAL)

COST_CHEAP = "cheap"
COST_EXPENSIVE = "expensive"   # 占 CPU/配额/花钱：每轮单独限次
COST_CLASSES = (COST_CHEAP, COST_EXPENSIVE)

# ==================== 数据来源（T2a） ====================
# 这三个字段回答的是同一个问题的三个面：**这份数据是谁给的、有多可信、挂了怎么办**。
#
# 为什么必须写进声明而不是留在 handler 里：模型看到的两段 JSON 长得一模一样，
# 但"我们自己数据库里的事实"和"某网站正文里的一句话"该被对待的方式完全不同 ——
# 前者可以直接引用，后者只能当线索，还可能夹带指令。这类区别**必须在声明里可见**，
# 否则它只存在于写代码那个人的脑子里。
PROVENANCE_INTERNAL = "internal"   # 我们自己的库 / 自己算出来的
PROVENANCE_EXTERNAL = "external"   # 第三方数据源（新闻、快讯、财报摘要…）
PROVENANCES = (PROVENANCE_INTERNAL, PROVENANCE_EXTERNAL)

TRUST_HIGH = "high"
TRUST_MEDIUM = "medium"            # 第三方**结构化**数据（行情数字、财报指标）：内容本身不含正文
TRUST_LOW = "low"                  # 第三方**正文**：只能当线索，且必须按"不可信内容"处理
TRUSTS = (TRUST_HIGH, TRUST_MEDIUM, TRUST_LOW)


@dataclass(frozen=True)
class ToolSpec:
    """一个工具的完整声明。

    handler 的契约刻意保持 `handler(name, args) -> payload`：
    - 身份/配额等上下文经 `agent.tool_scope` 的 contextvar 传入（并发隔离有测试）；
    - 这样既有的测试替身（两参数的假工具）继续可用，重构不会把测试改成"测替身"。
    """

    name: str
    namespace: str
    description: str
    parameters: dict
    handler: Callable[[str, dict], dict]
    layer: str = LAYER_INTEGRATION
    version: str = "1.0"
    scopes: tuple = ()
    side_effect: str = SIDE_EFFECT_NONE
    # None = 未声明（读操作天然幂等，不必声明）；有副作用的工具**必须显式声明**，
    # 因为"能不能安全重放"不能靠默认值猜 —— 猜错就是重复下单/重复扣款。
    # key = 调用方会给幂等键；natural = 本身幂等（如"置状态为 active"）
    idempotency: Optional[str] = None
    timeout_s: float = 15.0
    retryable: bool = True
    # {"ttl_s": 3600, "key_fields": (...)}；None = 不缓存
    cache: Optional[dict] = None
    # 可选：把参数规范成"语义等价即同一个键"的形式（默认按 JSON 规范序比较原文）。
    # 为什么需要：模型给的参数与内部归一化后的参数可能差一批默认值
    # （例如策略 JSON 补齐 initial_capital/data/position/risk），
    # 按原文做键会把同一次重算再算一遍 —— 这类重复在真实跑批里被实测到过。
    cache_key: Optional[Callable[[dict], str]] = None
    cost_class: str = COST_CHEAP
    approval: str = APPROVAL_NONE
    # 进上下文的字符预算（0 = 用 tool_contract 的默认值）
    result_budget_chars: int = 0
    # 审计里要脱敏的字段名（L3 必填，其它也无害）
    redaction: tuple = ()
    # 复杂参数的用法示例：schema 只能表达"结构合法"，示例才能教"什么时候填什么"
    examples: tuple = ()
    tags: tuple = field(default_factory=tuple)

    # ---------- 数据来源（T2a） ----------

    provenance: str = PROVENANCE_INTERNAL
    trust: str = TRUST_HIGH
    # 外部数据集名（见 `agent/external_source.py` 的 DATASETS）。
    # 声明了它，硬件上就自动获得：取数门禁、分级 TTL、不可用时**从装填里摘除**、
    # 结果进上下文时包成 `<external_data>` 数据块。
    source: Optional[str] = None

    # ---------- 派生 ----------

    @property
    def qualified_name(self) -> str:
        return f"{self.namespace}.{self.name}"

    @property
    def is_action(self) -> bool:
        return self.layer == LAYER_ACTION

    def schema(self, include_examples: bool = True) -> dict:
        """OpenAI 兼容的 function schema（模型看到的那份）。

        <h3>示例为什么拼进 description</h3>
        Anthropic 的 API 有独立的 `input_examples` 字段，但**OpenAI 兼容协议没有**
        （我们用的是 DeepSeek）。所以示例只能进描述文本 —— 这不影响它的价值：
        JSON Schema 只能表达"结构合法"，示例才能教"什么时候填什么"、
        "哪些字段是配套的"（例如策略 JSON 里 stop_loss_pct 用负数表示幅度）。

        `include_examples=False` 用于 A/B 测量：没有对照，就没法回答
        "加示例到底有没有让参数一次通过率变高"。
        """
        description = self.description
        if include_examples and self.examples:
            lines = [description, "", "示例："]
            for example in self.examples:
                lines.append(f"- {json.dumps(example, ensure_ascii=False)}")
            description = "\n".join(lines)
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": description,
                "parameters": self.parameters,
            },
        }

    def audit_fields(self) -> tuple:
        """审计时保留哪些参数。默认全部，敏感字段换成占位符。"""
        return tuple(self.redaction)


class ToolRegistry:
    """工具注册表：声明、查找、按工具集装填、启动自检。"""

    def __init__(self):
        self._specs: dict = {}
        self._order: list = []

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._specs:
            raise ValueError(f"工具重名：{spec.name}")
        self._specs[spec.name] = spec
        self._order.append(spec.name)
        return spec

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._specs.get(name)

    def names(self) -> list:
        return list(self._order)

    def specs(self) -> list:
        return [self._specs[name] for name in self._order]

    def namespaces(self) -> list:
        seen = []
        for spec in self.specs():
            if spec.namespace not in seen:
                seen.append(spec.namespace)
        return seen

    def schemas(self, enabled: Optional[Iterable[str]] = None,
                include_examples: bool = True) -> list:
        """按工具集装填（enabled=None 表示全部）。

        T1 起这个能力接进了 agent 循环：工具越多，一次性把所有定义塞进上下文越亏 ——
        定义本身就是上下文成本。`include_examples=False` 供 A/B 测量用。
        """
        wanted = set(enabled) if enabled is not None else None
        return [spec.schema(include_examples=include_examples) for spec in self.specs()
                if wanted is None or spec.name in wanted]

    def by_tag(self, tag: str) -> list:
        return [spec.name for spec in self.specs() if tag in spec.tags]

    # ---------- 启动自检 ----------

    def validate(self) -> list:
        """返回声明中的矛盾之处（空列表 = 全部合法）。
        这是"契约"能落地的关键：光有字段没用，得有人检查它们不互相矛盾。
        做法上只在启动时**告警**、不抛异常 —— 一个声明写错不该让整个服务起不来；
        测试里断言它为空，于是 CI 会拦住。
        """
        problems = []
        for spec in self.specs():
            where = f"{spec.name}"
            if spec.layer not in LAYERS:
                problems.append(f"{where}: 未知 layer={spec.layer}")
            if spec.side_effect not in SIDE_EFFECTS:
                problems.append(f"{where}: 未知 side_effect={spec.side_effect}")
            if spec.approval not in APPROVALS:
                problems.append(f"{where}: 未知 approval={spec.approval}")
            if spec.cost_class not in COST_CLASSES:
                problems.append(f"{where}: 未知 cost_class={spec.cost_class}")
            if spec.provenance not in PROVENANCES:
                problems.append(f"{where}: 未知 provenance={spec.provenance}")
            if spec.trust not in TRUSTS:
                problems.append(f"{where}: 未知 trust={spec.trust}")
            if spec.timeout_s <= 0:
                problems.append(f"{where}: timeout_s 必须为正")

            # 外部来源必须说清三件事：依赖哪个数据集、内容可不可信、成本算谁的。
            # 缺一件，第三方内容就会以内部事实的身份混进上下文（模型分不出来）。
            if spec.provenance == PROVENANCE_EXTERNAL:
                if not spec.source:
                    problems.append(f"{where}: 外部来源必须声明 source（装填摘除与取数门禁都靠它）")
                if spec.trust == TRUST_HIGH:
                    problems.append(f"{where}: 外部来源不能标成 trust=high（第三方数据不该与我们自己的库同级）")
                if spec.cost_class != COST_EXPENSIVE:
                    problems.append(f"{where}: 外部来源必须标成 expensive（配额就是钱）")
            elif spec.source:
                problems.append(f"{where}: 声明了 source={spec.source} 却是内部来源（二者只能有一个）")

            # 副作用必须有人点头，且必须能审计、能幂等
            if spec.side_effect != SIDE_EFFECT_NONE:
                if spec.layer != LAYER_ACTION:
                    problems.append(f"{where}: 有副作用却不是 {LAYER_ACTION} 层")
                if spec.approval == APPROVAL_NONE:
                    problems.append(f"{where}: 有副作用却没有 approval")
                if not spec.idempotency:
                    problems.append(f"{where}: 有副作用却没有声明 idempotency（无法安全重放）")
                if spec.side_effect == SIDE_EFFECT_IRREVERSIBLE and spec.idempotency != "key":
                    problems.append(f"{where}: 不可逆动作必须用 idempotency=key")
                if not spec.scopes:
                    problems.append(f"{where}: 有副作用却没有声明 scopes（无法授权）")
            if spec.layer == LAYER_ACTION and spec.side_effect == SIDE_EFFECT_NONE:
                problems.append(f"{where}: {LAYER_ACTION} 层却没有副作用（分层说明选错了）")

            # 不可逆动作一律不允许自动重试（不确定的副作用绝不盲目重发）
            if spec.side_effect == SIDE_EFFECT_IRREVERSIBLE and spec.retryable:
                problems.append(f"{where}: 不可逆动作却标成可重试")
            if spec.layer == LAYER_ACTION and spec.retryable:
                problems.append(f"{where}: {LAYER_ACTION} 层默认不应自动重试")

            # 缓存声明必须与输入字段对得上，否则缓存键取不到值
            if spec.cache:
                key_fields = spec.cache.get("key_fields") or ()
                properties = (spec.parameters or {}).get("properties") or {}
                for key_field in key_fields:
                    if key_field not in properties:
                        problems.append(f"{where}: cache.key_fields 里的 {key_field} 不在入参里")
                if (spec.cache.get("ttl_s") or 0) <= 0:
                    problems.append(f"{where}: cache.ttl_s 必须为正")

            # 纯计算层不该有网络语义（超时给得很宽通常意味着它其实在等 IO）
            if spec.layer == LAYER_PURE and spec.timeout_s > 10:
                problems.append(f"{where}: {LAYER_PURE} 层给 {spec.timeout_s}s 超时，层选错或超时过宽")

            if not spec.scopes and spec.layer != LAYER_PURE:
                problems.append(f"{where}: {spec.layer} 层没有声明 scopes")

            # 示例必须自己先合法：教错格式的示例比没有示例更糟
            from agent.tool_pipeline import validate_args
            for index, example in enumerate(spec.examples or ()):
                _cleaned, problem = validate_args(spec, example)
                if problem:
                    problems.append(f"{where}: 第 {index + 1} 个示例不合法（{problem}）")
        return problems


def log_validation_problems(problems: list, logger) -> None:
    for problem in problems:
        logger.error("工具声明有问题：%s", problem)
