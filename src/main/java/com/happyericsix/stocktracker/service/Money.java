package com.happyericsix.stocktracker.service;

import java.math.BigDecimal;
import java.math.RoundingMode;

/**
 * 钱的类型与舍入口径（Java 侧）：与 {@code python-data-service/agent/execution_contract.py}
 * 的 {@code MoneyPolicy} 一一对应。
 *
 * <h3>为什么要有这个类</h3>
 * 净值恒等式 {@code equity == cash + shares × price} 要能**零容差**成立。用 {@code double}
 * 做结算时它是**结构上不可能**成立的：{@code 0.1 + 0.2 != 0.3}，一次买入就能让"现金 + 持仓市值"
 * 与记录的净值差出 1e-10 —— 单次看不出，但痕迹、对账、"净值什么时候真的变了"全都建在这上面。
 *
 * <h3>它的两个用途</h3>
 * <ol>
 *   <li>给痕迹（trace）提供**唯一的**舍入入口：痕迹是钱的证据，证据不能每处各 round 一次；</li>
 *   <li>为结算路径迁到 {@code BigDecimal}/{@code DECIMAL} 提供地基（见 §7.1.2）。</li>
 * </ol>
 *
 * <p>刻意不做的事：精度**不放进配置**。它与 {@link ExecutionContract#MONEY_POLICY_VERSION}
 * 绑定，改动就是一次口径变更（旧痕迹的 {@code money_policy_version} 会说明它属于哪一代）。
 */
public final class Money {

    private Money() {
    }

    /** 与 Python 侧 {@code MoneyPolicy.version} 一致：改动口径必须同时改这个数。 */
    public static final int POLICY_VERSION = 1;
    public static final int PRICE_SCALE = 4;
    public static final int AMOUNT_SCALE = 2;
    public static final int EQUITY_SCALE = 2;
    public static final int RETURN_SCALE = 4;
    /** Python 侧用 {@code ROUND_HALF_UP}，这里是同一个东西。 */
    public static final RoundingMode ROUNDING = RoundingMode.HALF_UP;

    /**
     * 任意数值 → 指定小数位。接受 {@code Number}（{@code Double} / {@code BigDecimal} 都行），
     * 并且**一律先走字符串**：{@code new BigDecimal(0.1)} 会把 double 的二进制误差原样带进来，
     * 而 {@code new BigDecimal("0.1")} 才是人写下的那个 0.1。
     *
     * <p>{@code null} 原样返回 {@code null}：缺值就是缺值，不补 0（补 0 会静默改变金额）。
     */
    public static BigDecimal quantize(Number value, int scale) {
        if (value == null) {
            return null;
        }
        return new BigDecimal(value.toString()).setScale(scale, ROUNDING);
    }

    public static BigDecimal price(Number value) {
        return quantize(value, PRICE_SCALE);
    }

    public static BigDecimal amount(Number value) {
        return quantize(value, AMOUNT_SCALE);
    }

    public static BigDecimal equity(Number value) {
        return quantize(value, EQUITY_SCALE);
    }

    public static BigDecimal ratePct(Number value) {
        return quantize(value, RETURN_SCALE);
    }

    /** 股数一律整数（整手由结算逻辑保证，不在这里凑整）。 */
    public static int shares(Number value) {
        if (value == null) {
            throw new IllegalArgumentException("股数不接受 null");
        }
        return new BigDecimal(value.toString()).intValue();
    }

    /** 净值恒等式：{@code equity == cash + shares × price}（零容差，与 Python 侧同一判定）。 */
    public static boolean equityIdentityHolds(Number cash, Number shares, Number price, Number equity) {
        if (cash == null || shares == null || price == null || equity == null) {
            return false;
        }
        BigDecimal computed = equity(new BigDecimal(cash.toString())
                .add(new BigDecimal(shares.toString()).multiply(new BigDecimal(price.toString()))));
        return computed.compareTo(equity(equity)) == 0;
    }
}
