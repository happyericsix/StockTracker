package com.happyericsix.stocktracker.dto;

/**
 * 一次价格刷新产出的"快照"：单只股票评估预警所需的"全局共享"数据
 *
 * 字段约束：只装 symbol 维度共享的数据（一份/股票）
 *  - 不装 buyPrice：那是用户私有的，每个 (user, symbol) 一份
 *  - PnlPercentEvaluator 需要 buyPrice 时从 alert.getUser() 那边拿
 *
 * 数据流：
 *  - StockPriceRefreshJob 拉完所有自选股（按 symbol 去重）→ 产出 List<RefreshedPrice>
 *  - 发 PricesRefreshedEvent
 *  - AlertEvaluationListener 收到后 toMap(symbol → ...) 不再冲突
 *  - 每条 alert 评估时，RefreshedPrice 提供"市场状态"，buyPrice 从 alert.user 拿"用户状态"
 */
public record RefreshedPrice(
        String symbol,
        double currentPrice,
        IndicatorData indicators   // RSI/MACD 等，price 类预警可 null
) {
}
