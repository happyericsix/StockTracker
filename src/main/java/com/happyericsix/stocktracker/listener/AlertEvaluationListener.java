package com.happyericsix.stocktracker.listener;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;
import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.repository.AlertRepository;
import com.happyericsix.stocktracker.service.AlertEvaluationService;
import com.happyericsix.stocktracker.service.evaluator.EvaluationResult;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

import java.util.List;
import java.util.Map;
import java.util.stream.Collectors;

/**
 * 价格刷新事件监听器：
 *  - 监听 PricesRefreshedEvent（由 StockPriceRefreshJob 发）
 *  - 拿到一份 RefreshedPrice 后，对所有 enabled=true 的 Alert 跑评估
 *  - 命中 → 调 AlertEvaluationService.tryTrigger() 走冷却 + 落库 + 推送
 *
 * 为什么是 Listener 而不是新的 @Scheduled Job：
 *  1. 价刷新和评估绑在同一个时点，逻辑上"价的快照 → 评估"是一体的
 *  2. 价刷新和评估频率自动一致，不会出现"价已变但评估没跑"或"评估跑在两次刷新之间"
 *  3. 单只股票挂掉不影响其他（try/catch 隔离）
 */
@Component
public class AlertEvaluationListener {

    private static final Logger log = LoggerFactory.getLogger(AlertEvaluationListener.class);

    private final AlertRepository alertRepo;
    private final AlertEvaluationService evaluationService;

    public AlertEvaluationListener(AlertRepository alertRepo,
                                   AlertEvaluationService evaluationService) {
        this.alertRepo = alertRepo;
        this.evaluationService = evaluationService;
    }

    @EventListener
    public void onPricesRefreshed(PricesRefreshedEvent event) {
        long t0 = System.currentTimeMillis();
        List<RefreshedPrice> prices = event.prices();
        if (prices == null || prices.isEmpty()) {
            log.debug("AlertEvaluationListener: empty price list, skip");
            return;
        }

        // 按 symbol 索引，O(1) 找每条 alert 对应的快照
        // merge 函数保底：即便上游去重漏掉也不会炸，直接保留第一条
        Map<String, RefreshedPrice> bySymbol = prices.stream()
                .collect(Collectors.toMap(
                        RefreshedPrice::symbol,
                        p -> p,
                        (a, b) -> a));

        List<Alert> enabled = alertRepo.findByEnabledTrue();
        int evaluated = 0;
        int triggered = 0;
        int errors = 0;

        for (Alert alert : enabled) {
            RefreshedPrice rp = bySymbol.get(alert.getStockSymbol());
            if (rp == null) continue;     // 这只股票本次没拉到价，跳过
            evaluated++;

            try {
                EvaluationResult result = evaluationService.evaluate(alert, rp);
                if (result.isTriggered()) {
                    if (evaluationService.tryTrigger(alert, rp, result)) {
                        triggered++;
                    }
                }
            } catch (Exception e) {
                // 单条 alert 异常不影响其他
                errors++;
                log.error("Alert {} ({}) failed: {}", alert.getId(), alert.getStockSymbol(), e.getMessage(), e);
            }
        }

        long cost = System.currentTimeMillis() - t0;
        log.info("AlertEvaluationListener done: evaluated={}, triggered={}, errors={}, cost={}ms",
                evaluated, triggered, errors, cost);
    }
}
