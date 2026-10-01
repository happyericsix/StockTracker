package com.happyericsix.stocktracker.dto;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;

/**
 * 决策日志（trade journal）：**AI/策略当时说了什么 vs 后来实际发生了什么**。
 *
 * <p>这是"模型前向验证"的产品化落地（调研里 daily_stock_analysis 的
 * trade-journal 自证）：信号发出去不是终点，第二天收盘价回来对答案，
 * 命中率摆在一个用户看得见的地方——模型的可信度由记录说话，不由自述说话。
 *
 * <h3>口径的诚实边界（页面文案必须同步说明）</h3>
 * 判定用的是<b>次日收盘价相对决策日收盘价的方向</b>：
 * <ul>
 *   <li>它量的是"方向对了没有"，<b>不是"赚没赚到钱"</b>——真实成交是次日开盘
 *       （T+1）、有整手与税费约束，PnL 在模拟盘账户里另有口径；</li>
 *   <li>当天既有盘中成交又有日频 skip（already_traded_today）时，这一天按
 *       <b>成交那条</b>记录——"它选择不动"的痕迹仍在，但对答案要对在动作上；</li>
 *   <li>行情源取不到日线时判 {@code NO_PRICE}，<b>绝不猜方向</b>。</li>
 * </ul>
 */
public record TradeJournalResponse(
        Long strategyId,
        String strategyName,
        String symbol,
        /** 有方向性的判定次数（buy/sell 且已能对答案） */
        int evaluated,
        int hits,
        int misses,
        /** 决策日之后还没有收盘价（最近的决策，对不了答案） */
        int pending,
        /** 选择不动的天数（展示但计入分母之外） */
        int skips,
        /** hits/(hits+misses) 百分比一位小数；分母为 0 时 null（不显示胜率比编 0% 强） */
        BigDecimal hitRate,
        List<Entry> entries) {

    public static final String VERDICT_HIT = "hit";
    public static final String VERDICT_MISS = "miss";
    public static final String VERDICT_PENDING = "pending";
    public static final String VERDICT_SKIP = "skip";
    /** 行情源没给到日线：宁可标"没法判定"，不猜 */
    public static final String VERDICT_NO_PRICE = "no_price";

    public record Entry(
            LocalDate tradeDate,
            String decision,
            String signal,
            String skipReason,
            String decisionMode,
            /** 决策日收盘价（判定的基准价） */
            BigDecimal closeAtDecision,
            /** 下一交易日收盘价 */
            BigDecimal nextClose,
            /** 次日涨跌幅（百分点，两位小数） */
            BigDecimal nextChangePct,
            String verdict) {
    }
}
