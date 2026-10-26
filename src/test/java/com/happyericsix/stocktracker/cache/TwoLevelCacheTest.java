package com.happyericsix.stocktracker.cache;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import org.junit.jupiter.api.Test;
import org.springframework.cache.concurrent.ConcurrentMapCache;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 两级缓存的并发合并（singleflight）回归钉：
 * K 线页高频切换曾把同 key 的并发 miss 放大成 N 次 Python/akshare 调用
 * （缓存击穿），get(key, loader) 必须保证同一 key 的并发加载只执行一次。
 */
class TwoLevelCacheTest {

    private final Cache<Object, Object> caffeine = Caffeine.newBuilder().build();
    private final org.springframework.cache.Cache redis = new ConcurrentMapCache("test");
    private final TwoLevelCache cache = new TwoLevelCache("test", caffeine, redis, false);

    @Test
    void loadsOnceAndPopulatesBothLevels() {
        AtomicInteger loads = new AtomicInteger();
        String v = cache.get("k", () -> {
            loads.incrementAndGet();
            return "v1";
        });
        assertEquals("v1", v);
        assertEquals(1, loads.get());
        // 两级都写入了：L1 直接命中，L2 对新实例可见
        assertTrue(caffeine.getIfPresent("test::k") != null);
        assertTrue(redis.get("k") != null);
    }

    @Test
    void concurrentMissesCoalesceIntoSingleLoad() throws Exception {
        AtomicInteger loads = new AtomicInteger();
        CountDownLatch start = new CountDownLatch(1);
        int threads = 8;
        ExecutorService pool = Executors.newFixedThreadPool(threads);
        try {
            Future<?>[] futures = new Future<?>[threads];
            for (int i = 0; i < threads; i++) {
                futures[i] = pool.submit(() -> {
                    start.await();
                    return cache.get("hot", () -> {
                        loads.incrementAndGet();
                        Thread.sleep(80);   // 模拟一次秒级外部调用的窗口
                        return "v";
                    });
                });
            }
            start.countDown();
            for (Future<?> f : futures) {
                assertEquals("v", f.get(5, TimeUnit.SECONDS));
            }
            assertEquals(1, loads.get(), "并发 miss 必须合并成一次加载");
        } finally {
            pool.shutdownNow();
        }
    }

    @Test
    void nullLoadResultIsNotCachedAndRetried() {
        AtomicInteger loads = new AtomicInteger();
        assertNull(cache.get("n", () -> {
            loads.incrementAndGet();
            return null;
        }));
        assertNull(cache.get("n", () -> {
            loads.incrementAndGet();
            return null;
        }));
        assertEquals(2, loads.get(), "失败(null)不得被缓存住");
        assertNull(caffeine.getIfPresent("test::n"));
        assertNull(redis.get("n"));
    }

    @Test
    void loaderFailureSurfacesAsValueRetrievalException() {
        assertThrows(org.springframework.cache.Cache.ValueRetrievalException.class,
                () -> cache.get("bad", () -> {
                    throw new IllegalStateException("upstream down");
                }));
    }

    @Test
    void secondLevelHitPromotesToFirstLevelWithoutLoading() {
        redis.put("only2", "from-redis");
        AtomicInteger loads = new AtomicInteger();
        assertEquals("from-redis", cache.get("only2", () -> {
            loads.incrementAndGet();
            return "loaded";
        }));
        assertEquals(0, loads.get());
        assertTrue(caffeine.getIfPresent("test::only2") != null);
    }
}
