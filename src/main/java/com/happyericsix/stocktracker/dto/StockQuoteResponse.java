package com.happyericsix.stocktracker.dto;

import com.fasterxml.jackson.annotation.JsonProperty;

public record StockQuoteResponse(
    @JsonProperty("Global Quote") GlobalQuote globalQuote,
    @JsonProperty("Note") String note
) {
    public record GlobalQuote(
            @JsonProperty("01. symbol") String symbol,
            @JsonProperty("05. price") String price,
            @JsonProperty("07. latest trading day") String lastTradingDay,
            @JsonProperty("name") String name,
            // 涨跌三兄弟：Python 数据源（akshare/腾讯行情）本来就解析了「昨收」「涨跌额」「涨跌幅」，
            // 之前这个 record 没有对应属性，Jackson 反序列化时把它们丢弃了，
            // 于是 StockResponse 也只能给前端 4 个字段、前端无从判断涨跌。
            // 别名必须与 python-data-service/models.py 的 GlobalQuote 完全一致。
            @JsonProperty("08. previous close") String previousClose,
            @JsonProperty("09. change") String change,
            @JsonProperty("10. change percent") String changePercent
    ) {}
}
