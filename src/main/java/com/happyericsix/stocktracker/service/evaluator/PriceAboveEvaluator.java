package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/**
 * 价格突破（向上）：currentPrice >= threshold
 * 边沿触发：armed=true 时条件满足才触发一次，触发后 armed=false
 * 重置：价格跌到 threshold*resetRatio 或经过 reArmHours 小时
 */
@Component
public class PriceAboveEvaluator implements AlertEvaluator {
    @Override
    public String supportedType() { return "price_above"; }

    @Override
    public EvaluationResult evaluate(Alert alert, RefreshedPrice data) {
        double price = data.currentPrice();
        boolean fire = ArmedResetPolicy.check(
                alert, price, ArmedResetPolicy.Direction.ABOVE, LocalDateTime.now());
        return fire ? AlertEvaluator.triggered(price) : AlertEvaluator.notTriggered();
    }
}
