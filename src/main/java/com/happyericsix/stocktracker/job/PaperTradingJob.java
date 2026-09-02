package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.service.PaperTradingService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Component;

@Component
public class PaperTradingJob {
    private static final Logger log = LoggerFactory.getLogger(PaperTradingJob.class);
    private final PaperTradingService paperTradingService;

    public PaperTradingJob(PaperTradingService paperTradingService) {
        this.paperTradingService = paperTradingService;
    }

    @Scheduled(cron = "0 30 15 * * MON-FRI", zone = "Asia/Shanghai")
    public void runDailyPaper() {
        try {
            paperTradingService.evaluateDaily();
        } catch (Exception e) {
            log.error("PaperTradingJob failed: {}", e.getMessage(), e);
        }
    }
}
