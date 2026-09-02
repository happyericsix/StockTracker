package com.happyericsix.stocktracker.dto;

import lombok.Builder;
import java.io.Serializable;

@Builder
public record StockResponse(
        String symbol,
        String name,
        String price,
        String lastUpdated) implements Serializable {
    private static final long serialVersionUID = 1L;
}