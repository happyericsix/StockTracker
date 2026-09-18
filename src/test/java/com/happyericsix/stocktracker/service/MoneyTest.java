package com.happyericsix.stocktracker.service;

import org.junit.jupiter.api.Test;

import java.math.BigDecimal;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 钱的精度口径（Java 侧）：与 Python 的 {@code MoneyPolicy} 同一套小数位与舍入。
 *
 * <h3>为什么必须走 BigDecimal</h3>
 * 净值恒等式要**零容差**成立，而 {@code double} 结构上做不到：
 * {@code 0.1 + 0.2 != 0.3}。单次差 1e-17 看不出，但痕迹、对账、日度净值曲线
 * 全都建在"这条等式成立"上。这一层是模拟盘的钱能不能被信任的地基。
 */
class MoneyTest {

    @Test
    void scalesFollowTheDeclaredPolicy() {
        assertEquals(4, Money.PRICE_SCALE);
        assertEquals(2, Money.AMOUNT_SCALE);
        assertEquals(2, Money.EQUITY_SCALE);
        assertEquals(4, Money.RETURN_SCALE);
    }

    @Test
    void aDoubleBecomesTheNumberSomeoneWroteDownNotItsBinaryGhost() {
        // new BigDecimal(0.1) = 0.1000000000000000055511151231257827021181583404541015625
        assertEquals("0.1000", Money.price(0.1).toPlainString());
        assertEquals("0.3000", Money.price(0.1 + 0.2).toPlainString(),
                "先转字符串才能得到人写下的那个数");
    }

    @Test
    void amountsAreRoundedHalfUp() {
        assertEquals("10.13", Money.amount(10.125).toPlainString());
        assertEquals("10.12", Money.amount(10.124).toPlainString());
    }

    @Test
    void aBigDecimalPassesThroughWithoutLosingItsScale() {
        assertEquals("1234.5678", Money.price(new BigDecimal("1234.5678")).toPlainString());
    }

    @Test
    void nullStaysNullInsteadOfBecomingZero() {
        // 缺值补 0 会静默改变金额：它会把"不知道"变成"零元"
        assertNull(Money.price(null));
        assertNull(Money.amount(null));
        assertNull(Money.equity(null));
    }

    @Test
    void theEquityIdentityHoldsExactly() {
        // 10000 元本金、10 元买入 900 股（含佣金 100 元）→ 现金 100，持仓 900 股
        assertTrue(Money.equityIdentityHolds(new BigDecimal("100.00"),
                new BigDecimal("900"), new BigDecimal("11.00"), new BigDecimal("10000.00")));
    }

    @Test
    void theEquityIdentityCatchesTheDoubleDriftItExistsFor() {
        // 用 double 走一遍同样的账：分位上的漂移会让"逐笔对上账"变成不可能
        double cash = 10000.0 - 900 * 10.0 - 100.0;
        double equity = cash + 900 * 10.0;
        assertFalse(Money.equityIdentityHolds(cash, 900, 10.0, equity + 0.005),
                "差 5 厘也必须判不成立 —— 容差一旦放进去，它就再也回不来了");
    }

    @Test
    void sharesAreIntegers() {
        assertEquals(900, Money.shares(900.0));
        assertEquals(900, Money.shares(new BigDecimal("900.9999")));
    }
}
