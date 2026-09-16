package com.happyericsix.stocktracker.listener;

import com.happyericsix.stocktracker.event.PricesRefreshedEvent;
import com.happyericsix.stocktracker.service.PaperTradingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

@Component
public class PaperTradingListener {

    private static final Logger log = LoggerFactory.getLogger(PaperTradingListener.class);

    private final PaperTradingService paperTradingService;

    public PaperTradingListener(PaperTradingService paperTradingService) {
        this.paperTradingService = paperTradingService;
    }

    @EventListener
    public void onPricesRefreshed(PricesRefreshedEvent event) {
        try {
            paperTradingService.evaluateRealtime();
        } catch (Exception e) {
            log.error("PaperTradingListener failed: {}", e.getMessage(), e);
        }
    }
}
