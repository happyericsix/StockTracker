package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.*;

/**
 * TrailingTakeProfitEvaluator 单元测试 — 不依赖 Spring/Docker
 */
class TrailingTakeProfitEvaluatorTest {

    private final TrailingTakeProfitEvaluator eval = new TrailingTakeProfitEvaluator();
    private final double price100 = 100.0;
    private final double price140 = 140.0;
    private final double price128 = 128.0;  // 回撤 8.6%
    private final double price130 = 130.0;

    private Alert newAlert(Double threshold, Double highWatermark, Boolean armed) {
        return Alert.builder()
                .stockSymbol("AAPL")
                .conditionType("trailing_take_profit")
                .threshold(threshold)
                .enabled(true)
                .highWatermark(highWatermark)
                .armed(armed != null ? armed : true)
                .build();
    }

    private RefreshedPrice price(double p) {
        return new RefreshedPrice("AAPL", p, (com.happyericsix.stocktracker.dto.IndicatorData) null);
    }

    @Test
    @DisplayName("首次评估:highWatermark=null → 用 currentPrice 初始化,armed=true,不触发")
    void firstTime() {
        Alert alert = newAlert(5.0, null, true);
        EvaluationResult r = eval.evaluate(alert, price(price100));
        assertFalse(r.isTriggered());
        assertEquals(price100, alert.getHighWatermark());
    }

    @Test
    @DisplayName("涨到新高:highWatermark 跟着更新")
    void newHigh() {
        Alert alert = newAlert(5.0, price100, true);
        eval.evaluate(alert, price(price140));
        assertEquals(price140, alert.getHighWatermark());
    }

    @Test
    @DisplayName("从最高点回撤 ≥ 阈值 → 触发,armed=false,highWatermark 锁定")
    void triggerOnDrawdown() {
        Alert alert = newAlert(5.0, price140, true);
        EvaluationResult r = eval.evaluate(alert, price(price128));
        assertTrue(r.isTriggered());
        assertEquals(8.6, r.triggerValue(), 0.05);
        assertFalse(alert.getArmed());
        assertEquals(price140, alert.getHighWatermark(), "触发后 highWatermark 不变");
    }

    @Test
    @DisplayName("触发后继续跌:armed=false → 不再触发(避免重复)")
    void noRetriggerAfterTrigger() {
        Alert alert = newAlert(5.0, price140, false);
        EvaluationResult r = eval.evaluate(alert, price(115.0));
        assertFalse(r.isTriggered());
    }

    @Test
    @DisplayName("创新高 → re-arm")
    void reArmOnNewHigh() {
        Alert alert = newAlert(5.0, price140, false);
        // 反弹到 145,创新高
        eval.evaluate(alert, price(145.0));
        assertTrue(alert.getArmed());
        assertEquals(145.0, alert.getHighWatermark());
    }

    @Test
    @DisplayName("re-arm 后再回撤 → 触发新一轮")
    void newRoundTrigger() {
        Alert alert = newAlert(5.0, price140, false);
        eval.evaluate(alert, price(145.0));  // 创新高,re-arm
        EvaluationResult r = eval.evaluate(alert, price(130.0));  // 回撤 (145-130)/145 = 10.3%
        assertTrue(r.isTriggered());
        assertEquals(10.3, r.triggerValue(), 0.05);
    }

    @Test
    @DisplayName("回撤 < 阈值 → 不触发,armed 仍 true")
    void noTriggerSmallDrawdown() {
        Alert alert = newAlert(5.0, price140, true);
        // 当前价 138,回撤 (140-138)/140 = 1.4% < 5%
        EvaluationResult r = eval.evaluate(alert, price(138.0));
        assertFalse(r.isTriggered());
        assertTrue(alert.getArmed());
    }

    @Test
    @DisplayName("价格创新高(= highWatermark)→ 不算严格创新高,不 re-arm")
    void equalToHwmNotNewHigh() {
        Alert alert = newAlert(5.0, price140, false);
        eval.evaluate(alert, price(price140));  // 严格等于,不创新高
        assertFalse(alert.getArmed(), "用 > 严格比较,= 不算新高");
    }

    @Test
    @DisplayName("threshold 非法时,evaluator 不做拦截(由 DTO @Valid 负责)")
    void invalidThresholdNoInterceptor() {
        // 设计上:业务规则校验在 DTO 层,evaluator 只做防御性 null check
        // 即使 DTO 没拦住,evaluator 也不应抛 NPE
        Alert alert = newAlert(-5.0, price100, true);
        EvaluationResult r = eval.evaluate(alert, price(price100));
        // 这里不假设是 true 还是 false(因为算法仍能跑),
        // 关键是:不抛异常,返回 EvaluationResult
        assertNotNull(r);
    }

    @Test
    @DisplayName("threshold=null → 防御性返回 notTriggered,无 NPE")
    void nullThreshold() {
        Alert alert = newAlert(null, price100, true);
        EvaluationResult r = eval.evaluate(alert, price(price100));
        assertFalse(r.isTriggered());
    }
}
