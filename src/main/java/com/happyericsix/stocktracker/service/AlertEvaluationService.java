package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.Alert;
import com.happyericsix.stocktracker.entity.FavoriteStock;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

@Service
public class AlertEvaluationService {
    private static final Logger log = LoggerFactory.getLogger(AlertEvaluationService.class);

    public boolean evaluate(Alert alert, FavoriteStock favorite, double currentPrice) {

        if (!"pnl_percent".equals(alert.getConditionType())) {
            return false;
        }
        if (favorite.getBuyPrice() == null || favorite.getBuyPrice() <= 0) {
            log.warn("预警 {} 没有有效买入价，跳过评估", alert.getId());
            return false;
        }
        double buyPrice = favorite.getBuyPrice();
        double pnl = (currentPrice - buyPrice) / buyPrice * 100;
        return alert.getThreshold() > 0
                ? pnl >= alert.getThreshold()
                : pnl <= alert.getThreshold();
    }
}
