package com.happyericsix.stocktracker.listener;

import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.service.PaperTradingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Component;

@Component
public class PaperTradingListener {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingListener.class);

    private final PaperTradingService paperTradingService;

    public PaperTradingListener(PaperTradingService paperTradingService) {
        this.paperTradingService = paperTradingService;
    }

    /**
     * @Async（settlementExecutor）：这条监听里是逐策略最长 60s 的 Python 结算，
     * 同步监听会跑在单线程调度器上 —— 和价格刷新、预警评估互相排队，
     * Python 一慢 fixedRate 任务就持续滞后。
     */
    @Async("settlementExecutor")
    @EventListener
    public void onPricesRefreshed(PricesRefreshedEvent event) {
        try {
            paperTradingService.evaluateRealtime();
        } catch (Exception e) {
            log.error("PaperTradingListener failed: {}", e.getMessage(), e);
        }
    }
}
