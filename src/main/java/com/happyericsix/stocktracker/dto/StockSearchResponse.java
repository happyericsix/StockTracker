package com.happyericsix.stocktracker.dto;

import java.io.Serializable;
import java.util.List;

/**
 * 股票搜索响应（匹配 Python StockSearchResponse）。
 * 实现 Serializable：Redis 缓存使用 JdkSerializationRedisSerializer，被缓存对象必须可序列化。
 */
public record StockSearchResponse(String keyword, int count, List<StockSearchItem> results)
        implements Serializable {
    private static final long serialVersionUID = 1L;
}
