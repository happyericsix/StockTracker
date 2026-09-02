package com.happyericsix.stocktracker.dto;

public record DailyStockResponse(
        String date,
        String open,
        String close,
        String high,
        String low,
        long volume
) {}
