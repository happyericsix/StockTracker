package com.happyericsix.stocktracker.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 线程池配置：
 *  - taskExecutor        LLM 异步调用（@Async 用）
 *  - priceRefreshExecutor 价格刷新限并发（手动 CompletableFuture 用）
 *
 * 限 8 并发的依据：默认 protect Python akshare 服务不被瞬时打满，
 * 实测 8 在本机能压满 akshare 但不丢包，可按机器配置再调
 */
@Configuration
@EnableAsync
public class AsyncConfig {

    @Bean(name = "taskExecutor")
    public ThreadPoolTaskExecutor taskExecutor() {
        ThreadPoolTaskExecutor executor = new ThreadPoolTaskExecutor();
        executor.setCorePoolSize(5);
        executor.setMaxPoolSize(20);
        executor.setQueueCapacity(100);
        executor.setThreadNamePrefix("llm-chat-");
        executor.initialize();
        return executor;
    }

    /**
     * 价格刷新专用线程池：
     *  - 固定 8 线程（限并发，保护下游 Python 服务）
     *  - daemon 线程，不阻塞 JVM 关闭
     *  - 显式 shutdown 配合 destroyMethod，Spring 关闭时优雅退出
     */
    @Bean(name = "priceRefreshExecutor", destroyMethod = "shutdown")
    public ExecutorService priceRefreshExecutor() {
        AtomicInteger counter = new AtomicInteger();
        return Executors.newFixedThreadPool(8, r -> {
            Thread t = new Thread(r, "price-refresh-" + counter.incrementAndGet());
            t.setDaemon(true);
            return t;
        });
    }
}
