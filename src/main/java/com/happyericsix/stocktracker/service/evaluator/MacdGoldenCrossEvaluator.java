package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.dto.IndicatorData;
import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/**
 * MACD 金叉：DIF 上穿 DEA，即 hist > 0
 * threshold 字段在边沿触发中未使用（事件型触发，effective threshold=0）
 * 重置：hist <= 0（即金叉消失/反向） 或 经过 reArmHours 小时
 */
@Component
public class MacdGoldenCrossEvaluator implements AlertEvaluator {
    @Override
    public String supportedType() { return "macd_golden_cross"; }

    @Override
    public EvaluationResult evaluate(Alert alert, RefreshedPrice data) {
        IndicatorData ind = data.indicators();
        if (ind == null || ind.getMacdHist() == null) {
            return AlertEvaluator.notTriggered("no MACD data");
        }
        double hist = ind.getMacdHist();
        boolean fire = ArmedResetPolicy.check(
                alert, hist, ArmedResetPolicy.Direction.ABOVE, LocalDateTime.now());
        return fire ? AlertEvaluator.triggered(hist) : AlertEvaluator.notTriggered();
    }
}
