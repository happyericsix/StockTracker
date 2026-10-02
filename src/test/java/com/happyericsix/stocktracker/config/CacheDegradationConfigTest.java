package com.happyericsix.stocktracker.config;

import org.junit.jupiter.api.Test;
import org.springframework.cache.Cache;
import org.springframework.cache.concurrent.ConcurrentMapCache;

/**
 * 缓存降级：Redis 故障必须被翻译成"未命中/未缓存"，绝不能抛出去。
 * 这是对"裸机没起 Redis 时全部行情接口 500"那次实测故障的回归钉。
 */
class CacheDegradationConfigTest {

    private final Cache cache = new ConcurrentMapCache("stockSearch");
    private final CacheDegradationConfig config = new CacheDegradationConfig();
    private final RuntimeException connectionRefused =
            new RuntimeException("Connection refused: localhost/6379");

    @Test
    void getErrorIsSwallowedAndContinues() {
        // Spring 7：void 签名，错误被吞掉即"未命中"语义，业务方法照常执行
        config.errorHandler().handleCacheGetError(connectionRefused, cache, "茅台");
    }

    @Test
    void putEvictClearErrorsNeverPropagate() {
        var handler = config.errorHandler();
        handler.handleCachePutError(connectionRefused, cache, "茅台", "value");
        handler.handleCacheEvictError(connectionRefused, cache, "茅台");
        handler.handleCacheClearError(connectionRefused, cache);
        // 走到这里没抛就是通过：任何一条抛出都意味着接口会 500
    }

    @Test
    void nullCacheNameDoesNotBreakLogging() {
        var handler = config.errorHandler();
        handler.handleCacheGetError(connectionRefused, null, "key");
        // 缓存对象本身拿不到（极端情况）也不许在日志路径上抛 NPE
    }
}
