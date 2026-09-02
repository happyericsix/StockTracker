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
            @JsonProperty("name") String name
    ) {}
}
