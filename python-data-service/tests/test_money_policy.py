# -*- coding: utf-8 -*-
"""执行契约（3/3）：**钱的口径与对账**。

<h3>这个文件在防什么</h3>
"净值 = 现金 + 股数 × 价格"这条恒等式必须**零容差**成立 —— 它是"模拟盘的数字可信"
这一条承诺的技术底座。而现在的实现全程用 `double`（Java 侧现金/股数/佣金，
`paper_trades` 也存 double），一旦要逐笔对账，误差会累积成一个"说不清哪里来的差额"。

所以这里钉两件事：
1. **舍入口径单处定义**（价格 4 位 / 金额 2 位 / 净值 2 位 / 收益率 4 位、`ROUND_HALF_UP`），
   任何地方自己 `round()` 都算违约；
2. **逐笔累加与一次性求和必须完全相等**（浮点在这一点上必然分叉，Decimal 不会）。

真机对账（结算路径上的 `equity == cash + shares × price`）会随后续实现补齐 ——
本文件先钉住口径本身；实现落地后这里要加"用真实结算输出做恒等式断言"的用例。
"""
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent import execution_contract as ec  # noqa: E402


# ==================== 1. 精度口径 ====================


def test_scales_are_the_documented_ones():
    assert ec.MONEY.price_scale == 4
    assert ec.MONEY.amount_scale == 2
    assert ec.MONEY.equity_scale == 2
    assert ec.MONEY.return_scale == 4
    assert ec.MONEY.rounding == "HALF_UP"
    assert ec.MONEY.version == 1, "口径变更必须递增版本号（它写进执行指纹）"


def test_quantize_respects_each_scale():
    assert ec.MONEY.price("1.00005") == Decimal("1.0001")      # HALF_UP 进位
    assert ec.MONEY.price("1.00004") == Decimal("1.0000")
    assert ec.MONEY.amount("1.005") == Decimal("1.01")
    assert ec.MONEY.amount("1.004") == Decimal("1.00")
    assert ec.MONEY.equity("2.345") == Decimal("2.35")
    assert ec.MONEY.rate_pct("12.34567") == Decimal("12.3457")


def test_float_artifacts_are_absorbed():
    """浮点毛刺必须被吸收：`0.1 + 0.2` 是 0.30000000000000004，量化后应当就是 0.30。"""
    assert ec.MONEY.amount(0.1 + 0.2) == Decimal("0.30")
    assert ec.MONEY.price(1 / 3) == Decimal("0.3333")


def test_accepts_int_float_str_and_decimal():
    for value in (100, 100.0, "100", Decimal("100")):
        assert ec.MONEY.amount(value) == Decimal("100.00"), value


def test_rejects_none_and_bool():
    """布尔是 int 的子类，放过去会变成 1.00 —— 这类静默错必须挡住。"""
    for bad in (None, True, False):
        with pytest.raises(ValueError):
            ec.MONEY.amount(bad)
        with pytest.raises(ValueError):
            ec.MONEY.shares(bad)


def test_shares_are_integers():
    assert ec.MONEY.shares(100.0) == 100
    assert ec.MONEY.shares("99.9") == 99      # 向下取整，不四舍五入（整手由结算逻辑保证）
    assert isinstance(ec.MONEY.shares(Decimal("200")), int)


# ==================== 2. 净值恒等式 ====================


def test_equity_identity_holds_exactly():
    assert ec.equity_identity_holds("100000.00", 100, "1455.9915", "245599.15")
    assert ec.equity_identity_holds(0, 0, 10, 0)


def test_equity_identity_rejects_a_one_cent_difference():
    """零容差：差一分钱就是不对账，不许"约等于"。"""
    assert not ec.equity_identity_holds("100000.00", 100, "1455.9915", "245599.16")


# ==================== 3. 逐笔累加不得漂移 ====================


def test_sequential_and_at_once_sum_are_identical():
    """这条是"为什么必须用 Decimal"的直接证据：浮点路径在这里必然对不上账。"""
    amount = Decimal("12345.67")
    rate = Decimal("0.0003")          # 0.03% 佣金
    floor = Decimal("5.00")           # 最低佣金 5 元
    times = 1000

    one_by_one = Decimal("0")
    for _ in range(times):
        fee = max(ec.MONEY.amount(amount * rate), floor)
        one_by_one += ec.MONEY.amount(amount) + fee

    at_once = times * (ec.MONEY.amount(amount) + max(ec.MONEY.amount(amount * rate), floor))

    assert one_by_one == at_once
    # 逐笔 12345.67 + 最低佣金 5.00（0.03% 只有 3.70，被 5 元下限托住）= 12350.67
    assert one_by_one == Decimal("12350670.00"), one_by_one


def test_float_path_would_not_reconcile():
    """把同一笔账用 float 走一遍：结果与精确值不等 —— 这就是不能用 double 的原因。
    （这条断言不依赖运气：定点金额 + 定点费率，1000 次累加必然产生可见误差。）"""
    exact = Decimal("1000") * (ec.MONEY.amount(Decimal("12345.67"))
                               + max(ec.MONEY.amount(Decimal("12345.67") * Decimal("0.0003")),
                                     Decimal("5.00")))
    float_path = sum(12345.67 + max(12345.67 * 0.0003, 5.0) for _ in range(1000))
    assert Decimal(str(float_path)) != exact


def test_commission_floor_is_applied_on_the_quantized_amount():
    """最低佣金要用**量化后**的成交额判定，否则小额单会在边界上时收时不收。"""
    turnover = Decimal("1000.00")
    fee = max(ec.MONEY.amount(turnover * Decimal("0.0003")), Decimal("5.00"))
    assert fee == Decimal("5.00")

    big = Decimal("100000.00")
    fee = max(ec.MONEY.amount(big * Decimal("0.0003")), Decimal("5.00"))
    assert fee == Decimal("30.00")


# ==================== 4. 口径暴露 ====================


def test_describe_carries_the_money_policy_for_health():
    described = ec.MONEY.describe()
    assert described["version"] == ec.MONEY.version
    assert described["price_scale"] == 4
    assert ec.describe()["money_policy"] == described


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        if fn.__code__.co_argcount == 0:
            fn()
            print("PASS", fn.__name__)
