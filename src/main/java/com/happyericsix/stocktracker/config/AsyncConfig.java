package com.happyericsix.stocktracker.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.scheduling.annotation.EnableAsync;
import org.springframework.scheduling.concurrent.ThreadPoolTaskExecutor;

import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;

/**
 * 线程池配置：
 *  - taskExecutor            LLM 异步调用（@Async 用）
 *  - priceRefreshExecutor    价格刷新限并发（手动 CompletableFuture 用）
 *  - newsEnrichmentExecutor  资讯补数据（抓上游 + 分批解读），与取数池隔离
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
        // 队列满时由调用线程执行，避免 @Async 任务被静默丢弃（用户消息已落库却永远等不到回复）
        executor.setRejectedExecutionHandler(new ThreadPoolExecutor.CallerRunsPolicy());
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

    /**
     * 资讯补数据专用线程池（N5b：个股页"先给列表、解读后台补"）。
     *
     * <p>为什么和 {@code priceRefreshExecutor} 分开：那个池跑的是取数（毫秒~秒级），
     * 这个池跑的是 **LLM 往返**（秒级起步，一批 5 条）。混在一起时，
     * "刷自选股价"与"补资讯解读"会互相排队 —— 用户看到的正是"两个都半天不出来"。
     *
     * <p>并发为什么是 4：一只票的一轮补数据内部是**串行**的分批（每批一次模型调用），
     * 所以这个数实际等于"同时有几只票在补"。4 足以覆盖"用户来回切几只票"的用法，
     * 又不会在一次误操作（脚本狂刷不同代码）时把模型额度打满。
     *
     * <p>队列 64 + {@code DiscardOldestPolicy}：**不**用 CallerRunsPolicy。
     * 它在 {@code taskExecutor} 上是对的（消息已落库、绝不能被丢弃），但这里相反 ——
     * 让 Tomcat 请求线程去跑 30 条资讯的解读，等于把"接口立刻返回"这个改造原地取消掉。
     * 资讯补数据是**可重放**的（冷却窗口过后下一次打开页面会再来一遍），丢掉最老的排队项是安全的。
     */
    @Bean(name = "newsEnrichmentExecutor", destroyMethod = "shutdown")
    public ExecutorService newsEnrichmentExecutor() {
        AtomicInteger counter = new AtomicInteger();
        return new ThreadPoolExecutor(
                4, 4, 60L, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(64),
                r -> {
                    Thread t = new Thread(r, "news-enrich-" + counter.incrementAndGet());
                    t.setDaemon(true);
                    return t;
                },
                new ThreadPoolExecutor.DiscardOldestPolicy());
    }

    /**
     * 价格刷新事件的下游结算池（预警评估 + 模拟盘盘中结算的 @Async 监听器用）。
     *
     * <p>为什么不用默认 taskExecutor：那是要给用户聊天回复用的（LLM 往返），
     * 混进"逐策略 60s Python 结算"会把聊天排队挤爆；也不复用 priceRefreshExecutor
     * —— 那个池正被"拉价"占着，结算和拉价同池就回到了"互相排队"的老问题。
     *
     * <p>为什么 2 线程 + CallerRuns：结算内部本来就是逐策略串行循环，并行度 2
     * 足够消化；队列满时退回调度线程执行（即同步监听的原行为），保证不丢事件。
     */
    @Bean(name = "settlementExecutor", destroyMethod = "shutdown")
    public ExecutorService settlementExecutor() {
        AtomicInteger counter = new AtomicInteger();
        return new ThreadPoolExecutor(
                2, 2, 60L, TimeUnit.SECONDS,
                new ArrayBlockingQueue<>(32),
                r -> {
                    Thread t = new Thread(r, "settle-" + counter.incrementAndGet());
                    t.setDaemon(true);
                    return t;
                },
                new ThreadPoolExecutor.CallerRunsPolicy());
    }
}
