package com.happyericsix.stocktracker.dto;

/**
 * 自选股保存结果 DTO：绝不返回 JPA 实体（实体含 User 关联，直接序列化会泄漏密码哈希等字段）。
 */
public record FavoriteStockResponse(String symbol, Double buyPrice, Integer quantity, String buyDate) {
    public static FavoriteStockResponse from(
            String symbol, Double buyPrice, Integer quantity, String buyDate) {
        return new FavoriteStockResponse(symbol, buyPrice, quantity, buyDate);
    }
}
