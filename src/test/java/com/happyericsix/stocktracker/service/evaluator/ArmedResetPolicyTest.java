package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.entity.Alert;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.*;

/**
 * ArmedResetPolicy 单元测试 — 不依赖 Spring/Docker/MySQL/Redis
 * 跑法: .\mvnw.cmd test -Dtest=ArmedResetPolicyTest
 */
class ArmedResetPolicyTest {

    private Alert newAlert(boolean armed, Double threshold, Double resetRatio,
                           Integer reArmHours, LocalDateTime lastEvaluatedAt) {
        return Alert.builder()
                .stockSymbol("AAPL")
                .conditionType("price_above")
                .threshold(threshold)
                .enabled(true)
                .armed(armed)
                .resetRatio(resetRatio)
                .reArmHours(reArmHours)
                .lastEvaluatedAt(lastEvaluatedAt)
                .build();
    }

    @Test
    @DisplayName("首次评估:armed=true,条件满足 → 触发 + armed=false")
    void firstTimeTrigger() {
        Alert alert = newAlert(true, 100.0, 0.5, 2, null);
        boolean fire = ArmedResetPolicy.check(
                alert, 110.0, ArmedResetPolicy.Direction.ABOVE, LocalDateTime.now());
        assertTrue(fire);
        assertFalse(alert.getArmed());
    }

    @Test
    @DisplayName("armed=true,条件不满足 → 不触发")
    void notMet() {
        Alert alert = newAlert(true, 100.0, 0.5, 2, null);
        boolean fire = ArmedResetPolicy.check(
                alert, 90.0, ArmedResetPolicy.Direction.ABOVE, LocalDateTime.now());
        assertFalse(fire);
        assertTrue(alert.getArmed());
    }

    @Test
    @DisplayName("armed=false,价格回落到 resetRatio 区间 → re-arm")
    void priceReset() {
        Alert alert = newAlert(false, 100.0, 0.5, 2, LocalDateTime.now());
        boolean fire = ArmedResetPolicy.check(
                alert, 40.0, ArmedResetPolicy.Direction.ABOVE, LocalDateTime.now());
        assertFalse(fire, "回落后不应立即触发(条件仍满足)...");
        // 等等:armed=true 后条件满足会触发! 验证一下确实 re-arm 了
        assertTrue(alert.getArmed());
    }

    @Test
    @DisplayName("armed=false,时间超过 reArmHours → re-arm")
    void timeReset() {
        LocalDateTime longAgo = LocalDateTime.now().minusHours(5);
        Alert alert = newAlert(false, 100.0, 0.5, 2, longAgo);
        boolean fire = ArmedResetPolicy.check(
                alert, 110.0, ArmedResetPolicy.Direction.ABOVE, LocalDateTime.now());
        assertTrue(fire, "时间衰减后应 re-arm 并触发");
        assertFalse(alert.getArmed());
    }

    @Test
    @DisplayName("armed=false,既没回落到 resetRatio 也没到时间 → 仍 disarmed")
    void noReset() {
        Alert alert = newAlert(false, 100.0, 0.5, 2, LocalDateTime.now());
        boolean fire = ArmedResetPolicy.check(
                alert, 95.0, ArmedResetPolicy.Direction.ABOVE, LocalDateTime.now());
        assertFalse(fire);
        assertFalse(alert.getArmed());
    }

    @Test
    @DisplayName("BELOW 方向:armed=true,条件满足(<=)→ 触发")
    void belowTrigger() {
        Alert alert = newAlert(true, 50.0, 0.5, 2, null);
        boolean fire = ArmedResetPolicy.check(
                alert, 30.0, ArmedResetPolicy.Direction.BELOW, LocalDateTime.now());
        assertTrue(fire);
        assertFalse(alert.getArmed());
    }

    @Test
    @DisplayName("lastEvaluatedAt 每次都被更新")
    void lastEvaluatedUpdated() {
        LocalDateTime before = LocalDateTime.now().minusMinutes(10);
        Alert alert = newAlert(true, 100.0, 0.5, 2, before);
        LocalDateTime now = LocalDateTime.now();
        ArmedResetPolicy.check(alert, 90.0, ArmedResetPolicy.Direction.ABOVE, now);
        assertEquals(now, alert.getLastEvaluatedAt());
    }
}
