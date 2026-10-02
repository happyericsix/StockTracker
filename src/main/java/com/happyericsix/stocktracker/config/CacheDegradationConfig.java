package com.happyericsix.stocktracker.config;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.cache.Cache;
import org.springframework.cache.annotation.CachingConfigurer;
import org.springframework.cache.interceptor.CacheErrorHandler;
import org.springframework.context.annotation.Configuration;

import java.util.concurrent.atomic.AtomicLong;

/**
 * 缓存优雅降级：Redis 不可用时，@Cacheable 按"缓存未命中"继续，而不是炸接口。
 *
 * <h3>为什么必须有这个（实测故障）</h3>
 * 裸机部署常常没起 Redis（本机甚至没装）。默认的缓存拦截器会把
 * RedisConnectionFailureException 原样抛出，于是**所有走缓存的读**
 * （行情搜索/实时价/概况/指标，见 StockDataGateway）整体 500 ——
 * 用户看到的是"加自选搜不到任何东西"，而真相只是缓存层不在。
 *
 * <h3>语义</h3>
 * 缓存的本职就是"在，提速；不在，系统照常工作"。这里把缓存故障
 * 翻译回它的本义：GET 失败 = 未命中（直查上游），PUT/EVICT/CLEAR
 * 失败 = 没缓存上（下次再试）。代价只是每次都打上游，慢而不断。
 *
 * <h3>日志节流</h3>
 * Redis 掉线期间每个请求都会失败，不节流就是日志洪水（每秒几十条同样的
 * WARN）。60 秒最多记一条，恢复后自然安静。
 */
@Configuration
public class CacheDegradationConfig implements CachingConfigurer {

    private static final Logger log = LoggerFactory.getLogger(CacheDegradationConfig.class);
    private static final long LOG_THROTTLE_MS = 60_000;
    private static final AtomicLong LAST_LOG_MS = new AtomicLong();

    @Override
    public CacheErrorHandler errorHandler() {
        // 注意 Spring Framework 7 的签名：handleCacheGetError 不再有 boolean 返回值，
        // 错误被处理后拦截器一律按"未命中"继续执行业务方法 —— 正是我们要的语义。
        return new CacheErrorHandler() {
            @Override
            public void handleCacheGetError(RuntimeException exception, Cache cache, Object key) {
                throttledWarn("读缓存失败，按未命中继续直查上游", cache, exception);
            }

            @Override
            public void handleCachePutError(RuntimeException exception, Cache cache, Object key, Object value) {
                throttledWarn("写缓存失败，本次不缓存", cache, exception);
            }

            @Override
            public void handleCacheEvictError(RuntimeException exception, Cache cache, Object key) {
                throttledWarn("清缓存失败", cache, exception);
            }

            @Override
            public void handleCacheClearError(RuntimeException exception, Cache cache) {
                throttledWarn("清空缓存失败", cache, exception);
            }
        };
    }

    private static void throttledWarn(String action, Cache cache, RuntimeException exception) {
        long now = System.currentTimeMillis();
        long last = LAST_LOG_MS.get();
        if (now - last < LOG_THROTTLE_MS || !LAST_LOG_MS.compareAndSet(last, now)) {
            return;
        }
        log.warn("{}（cache={}，此后 60s 内同类失败静默）：{}", action,
                cache == null ? "?" : cache.getName(), exception.getMessage());
    }
}
