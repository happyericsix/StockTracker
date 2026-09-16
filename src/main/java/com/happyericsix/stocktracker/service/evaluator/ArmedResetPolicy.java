package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.entity.Alert;

import java.time.LocalDateTime;

/**
 * 边沿触发 + 时间/价格回落重置：统一 armed 状态机
 *
 * 核心三步（每次评估都跑）：
 *   1) 若 armed=false，检查价格回落 / 时间衰减，二选一即重置 armed=true
 *   2) 若 armed=true 且条件满足，触发（armed=false，返回 true）
 *   3) 仅在 armed 状态更新 lastEvaluatedAt（供下次时间衰减用）；
 *      disarmed 期间不得刷新，否则衰减基准会被持续评估不断推后而永不生效
 *
 * 副作用：直接修改 alert.armed / alert.lastEvaluatedAt
 * 设计上不接受 Spring 注入，全部 static；测试时手动传 now 即可
 */
public final class ArmedResetPolicy {

    /** 触发方向：指标值越过阈值时是"上穿"还是"下穿" */
    public enum Direction {
        /** 触发条件：metric >= threshold（如 price_above、pnl_percent 止盈、rsi_overbought、macd_golden_cross） */
        ABOVE,
        /** 触发条件：metric <= threshold（如 price_below、pnl_percent 止损、rsi_oversold、macd_death_cross） */
        BELOW
    }

    private static final double DEFAULT_RESET_RATIO = 0.5;
    private static final int DEFAULT_REARM_HOURS = 2;

    private ArmedResetPolicy() {}

    /**
     * 评估一次
     * @return true = 本次应当触发；false = 不触发
     */
    public static boolean check(Alert alert, double currentMetric, Direction direction, LocalDateTime now) {
        boolean armed = isArmed(alert);
        double threshold = nz(alert.getThreshold(), 0.0);
        double resetRatio = getResetRatio(alert);
        int reArmHours = getReArmHours(alert);

        // 1) 处于 disarmed 状态，先看能不能 re-arm
        if (!armed) {
            if (isPriceReset(currentMetric, threshold, resetRatio, direction)
                    || isTimeReset(alert.getLastEvaluatedAt(), now, reArmHours)) {
                alert.setArmed(true);
                armed = true;
            }
        }

        // 2) armed 且条件满足：触发 + disarm
        if (armed && isConditionMet(currentMetric, threshold, direction)) {
            alert.setArmed(false);
            alert.setLastEvaluatedAt(now);
            return true;
        }

        // 3) 仅在 armed 状态刷新 lastEvaluatedAt。
        //    注意：disarmed 期间绝不能刷新——评估每 5 分钟一次，若每次把基准推到现在，
        //    时间衰减重置（now - lastEvaluatedAt >= reArmHours）将永远无法成立（见 isTimeReset）。
        if (armed) {
            alert.setLastEvaluatedAt(now);
        }
        return false;
    }

    // ===== 内部判断 =====

    private static boolean isArmed(Alert alert) {
        Boolean a = alert.getArmed();
        // 兼容：v1 改造前的老数据 armed 为 null，按"可触发"处理
        return a == null || a;
    }

    private static double getResetRatio(Alert alert) {
        Double r = alert.getResetRatio();
        return r != null ? r : DEFAULT_RESET_RATIO;
    }

    private static int getReArmHours(Alert alert) {
        Integer h = alert.getReArmHours();
        return h != null ? h : DEFAULT_REARM_HOURS;
    }

    private static boolean isConditionMet(double metric, double threshold, Direction direction) {
        return direction == Direction.ABOVE ? metric >= threshold : metric <= threshold;
    }

    /**
     * 价格回落重置判断：
     *  - ABOVE（metric >= threshold 触发）：re-arm 当 metric <= threshold * resetRatio
     *    例：price_above threshold=100, resetRatio=0.5 → 跌到 50 即 re-arm
     *  - BELOW 且 threshold >= 0（price_below 等）：re-arm 当 metric >= threshold * (2 - resetRatio)
     *    例：price_below threshold=100, resetRatio=0.5 → 涨到 150 即 re-arm
     *  - BELOW 且 threshold < 0（如 pnl 止损 -10%）：re-arm 当 metric >= threshold * resetRatio
     *    例：pnl 止损 -10, resetRatio=0.5 → 反弹到 -5 即 re-arm
     */
    private static boolean isPriceReset(double metric, double threshold, double resetRatio, Direction direction) {
        if (direction == Direction.ABOVE) {
            return metric <= threshold * resetRatio;
        }
        if (threshold >= 0) {
            return metric >= threshold * (2.0 - resetRatio);
        }
        return metric >= threshold * resetRatio;
    }

    /**
     * 时间衰减重置：now - lastEvaluatedAt >= reArmHours 小时
     * lastEvaluatedAt 为 null 视作"早就衰减过了"，立即 re-arm
     */
    private static boolean isTimeReset(LocalDateTime lastEvaluatedAt, LocalDateTime now, int reArmHours) {
        if (lastEvaluatedAt == null) return true;
        return !now.isBefore(lastEvaluatedAt.plusHours(reArmHours));
    }

    private static double nz(Double v, double def) {
        return v != null ? v : def;
    }
}
