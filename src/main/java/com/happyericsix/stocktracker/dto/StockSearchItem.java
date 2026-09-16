package com.happyericsix.stocktracker.dto;

import java.io.Serializable;

/**
 * 股票搜索单条结果。
 */
public record StockSearchItem(String code, String name) implements Serializable {
    private static final long serialVersionUID = 1L;
}
