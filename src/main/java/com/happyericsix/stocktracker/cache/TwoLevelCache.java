package com.happyericsix.stocktracker.cache;

import com.github.benmanes.caffeine.cache.Cache;
import org.springframework.cache.support.AbstractValueAdaptingCache;
import org.springframework.lang.Nullable;

import java.util.concurrent.Callable;

public class TwoLevelCache extends AbstractValueAdaptingCache {

    private final String name;
    private final Cache<Object, Object> caffeineCache;
    private final org.springframework.cache.Cache redisCache;

    public TwoLevelCache(String name, Cache<Object, Object> caffeineCache,
                         org.springframework.cache.Cache redisCache, boolean allowNullValues) {
        super(allowNullValues);
        this.name = name;
        this.caffeineCache = caffeineCache;
        this.redisCache = redisCache;
    }

    @Override
    public String getName() {
        return name;
    }

    @Override
    public Object getNativeCache() {
        return this;
    }

    private Object caffeineKey(Object key) {
        return name + "::" + key;
    }

    @Override
    @Nullable
    protected Object lookup(Object key) {
        Object caffeineKey = caffeineKey(key);
        Object value = caffeineCache.getIfPresent(caffeineKey);
        if (value != null) {
            return value;
        }

        ValueWrapper wrapper = redisCache.get(key);
        if (wrapper != null) {
            value = wrapper.get();
            caffeineCache.put(caffeineKey, value);
            return value;
        }

        return null;
    }

    /**
     * 带并发合并的加载：同一 key 的并发 miss 只放一个线程真正执行 valueLoader
     * （借 Caffeine {@code get(key, function)} 的原子语义做 singleflight），
     * 其余线程阻塞等同一份结果。
     *
     * <p>没有合并的后果（K 线页高频切换实测路径）：缓存刚好过期的瞬间，
     * N 个并发请求同时 miss，各自穿透去调 Python/akshare（单次秒级），
     * 把一次击穿放大成 N 次外部调用 —— 排队、超时、雪崩的起点。
     * {@code @Cacheable(sync = true)} 会走到本方法。
     *
     * <p>valueLoader 返回 null（上游失败的显式约定）：两级缓存都不写，直接返回 null，
     * 调用方按"未取到"处理，失败不会被缓存住。
     */
    @Override
    public <T> T get(Object key, Callable<T> valueLoader) {
        Object value = lookup(key);
        if (value != null) {
            return (T) fromStoreValue(value);
        }

        Object loaded;
        try {
            loaded = caffeineCache.get(caffeineKey(key), k -> {
                // 排队期间结果可能已被写入（本实例 put，或别的实例回填 Redis）：再查一次，
                // 命中就不必重复加载。mapping 返回 null 表示"不缓存"（上游失败的约定）
                ValueWrapper wrapper = redisCache.get(key);
                if (wrapper != null) {
                    return wrapper.get();
                }
                try {
                    T result = valueLoader.call();
                    return result == null ? null : toStoreValue(result);
                } catch (Exception e) {
                    throw new ValueRetrievalException(key, valueLoader, e);
                }
            });
        } catch (ValueRetrievalException e) {
            throw e;
        } catch (RuntimeException e) {
            // Caffeine 正常会把 mapping function 的异常原样抛出；这里兜底保证
            // Spring Cache 的 get(key, loader) 契约（包装成 ValueRetrievalException）
            throw new ValueRetrievalException(key, valueLoader, e);
        }

        if (loaded == null) {
            return null;
        }
        redisCache.put(key, loaded);
        return (T) fromStoreValue(loaded);
    }

    @Override
    public void put(Object key, @Nullable Object value) {
        Object storeValue = toStoreValue(value);
        caffeineCache.put(caffeineKey(key), storeValue);
        redisCache.put(key, storeValue);
    }

    @Override
    @Nullable
    public ValueWrapper putIfAbsent(Object key, @Nullable Object value) {
        Object storeValue = toStoreValue(value);
        ValueWrapper existing = redisCache.putIfAbsent(key, storeValue);
        if (existing == null) {
            caffeineCache.put(caffeineKey(key), storeValue);
            return null;
        }
        caffeineCache.put(caffeineKey(key), existing.get());
        return existing;
    }

    @Override
    public void evict(Object key) {
        caffeineCache.invalidate(caffeineKey(key));
        redisCache.evict(key);
    }

    @Override
    public void clear() {
        caffeineCache.invalidateAll();
        redisCache.clear();
    }
}