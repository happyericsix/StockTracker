package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.dto.IndicatorData;
import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/** RSI 超卖：rsi < threshold（一般阈值 30） */
@Component
public class RsiOversoldEvaluator implements AlertEvaluator {
    @Override
    public String supportedType() { return "rsi_oversold"; }

    @Override
    public EvaluationResult evaluate(Alert alert, RefreshedPrice data) {
        IndicatorData ind = data.indicators();
        if (ind == null || ind.getRsi() == null) {
            return AlertEvaluator.notTriggered("no indicator data");
        }
        double rsi = ind.getRsi();
        boolean fire = ArmedResetPolicy.check(
                alert, rsi, ArmedResetPolicy.Direction.BELOW, LocalDateTime.now());
        return fire ? AlertEvaluator.triggered(rsi) : AlertEvaluator.notTriggered();
    }
}
