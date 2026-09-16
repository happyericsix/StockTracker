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
    @DisplayName("armed 状态评估会刷新 lastEvaluatedAt（供衰减计时）")
    void lastEvaluatedUpdated() {
        LocalDateTime before = LocalDateTime.now().minusMinutes(10);
        Alert alert = newAlert(true, 100.0, 0.5, 2, before);
        LocalDateTime now = LocalDateTime.now();
        ArmedResetPolicy.check(alert, 90.0, ArmedResetPolicy.Direction.ABOVE, now);
        assertEquals(now, alert.getLastEvaluatedAt());
    }

    @Test
    @DisplayName("回归:时间衰减基准不被持续评估推后——disarmed 后每5分钟评估,到 reArmHours 边界仍会重新触发")
    void timeDecayIsNotPushedForwardByFrequentEvaluations() {
        LocalDateTime t0 = LocalDateTime.of(2026, 1, 5, 9, 30);
        Alert alert = newAlert(true, 100.0, 0.5, 2, t0.minusMinutes(10));

        // 触发一次：armed=true 且价格 110 >= 100
        assertTrue(ArmedResetPolicy.check(alert, 110.0, ArmedResetPolicy.Direction.ABOVE, t0));
        assertFalse(alert.getArmed());

        // 此后价格一直 110（条件仍满足、但没回落到 resetRatio 区间），每 5 分钟评估一次
        LocalDateTime t = t0;
        boolean firedAgain = false;
        for (int i = 0; i < 60; i++) {
            t = t.plusMinutes(5);
            if (ArmedResetPolicy.check(alert, 110.0, ArmedResetPolicy.Direction.ABOVE, t)) {
                firedAgain = true;
                break;
            }
        }
        // reArmHours=2：衰减到点（约 11:30）应重新武装并触发；
        // 旧实现在 disarmed 期间每次都刷新 lastEvaluatedAt，导致永远等不到衰减
        assertTrue(firedAgain, "时间衰减到期后应重新触发");
    }
}
