package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;

/**
 * 跟踪止盈（Trailing Take-Profit）：从跟踪期间最高价回撤 N% 触发
 *
 * 核心机制（与 v1/v2 边沿触发不同 — 不复用 ArmedResetPolicy）：
 *   状态变量:highWatermark（跟踪期间最高价）
 *   触发条件:armed=true && (highWatermark - currentPrice) / highWatermark * 100 >= threshold
 *   重新武装:currentPrice > highWatermark（创新高 → 进入新一轮跟踪）
 *
 * 设计要点：
 *  1) 业务规则（threshold 范围、conditionType 白名单）在 DTO @Valid 层校验,
 *     evaluator 只做防御性 null check,不做业务校验
 *  2) 首次评估时 highWatermark=null，用 currentPrice 初始化
 *  3) 触发后 armed=false，但 highWatermark 保持不变（不回退）
 *  4) 每次评估都更新 highWatermark = max(highWatermark, currentPrice)
 *  5) "创新高" 用 > 严格比较（= 不算创新高，避免在最高点附近反复 re-arm）
 *  6) 不依赖 buyPrice / FavoriteStock：跟踪止盈只看价格本身
 *
 * 例子（AAPL 从 $100 涨到 $140 又跌到 $128，threshold=5%）：
 *  - T5 $140:highWatermark=$140，回撤 0%，不触发
 *  - T6 $128:回撤 (140-128)/140 = 8.6% ≥ 5% → 触发，armed=false
 *  - T7 $115:armed=false，回撤更大，沉默
 *  - T8 反弹到 $145:创新高 → highWatermark=$145, armed=true
 *  - T9 $130:回撤 (145-130)/145 = 10.3% → 触发新一轮
 */
@Component
public class TrailingTakeProfitEvaluator implements AlertEvaluator {

    @Override
    public String supportedType() { return "trailing_take_profit"; }

    @Override
    public EvaluationResult evaluate(Alert alert, RefreshedPrice data) {
        // 防御性 null check（DTO 已校验过，正常不会进来,但 evaluator 不能假设上游一定干净）
        if (data == null || alert == null || alert.getThreshold() == null) {
            return AlertEvaluator.notTriggered("missing required data");
        }
        double currentPrice = data.currentPrice();
        if (currentPrice <= 0) {
            return AlertEvaluator.notTriggered("invalid price");
        }
        double threshold = alert.getThreshold();  // DTO 已保证在 (0, 100)

        // 1) 初始化 / 读取 highWatermark
        Double hwmBoxed = alert.getHighWatermark();
        double hwm = (hwmBoxed == null || hwmBoxed <= 0) ? currentPrice : hwmBoxed;

        // 2) 算回撤
        double drawdown = (hwm - currentPrice) / hwm * 100.0;

        // 3) 评估触发
        boolean armed = !Boolean.FALSE.equals(alert.getArmed());
        if (armed && drawdown >= threshold) {
            alert.setArmed(false);
            alert.setLastEvaluatedAt(LocalDateTime.now());
            alert.setHighWatermark(hwm);  // 锁定为触发时的最高点
            return AlertEvaluator.triggered(drawdown);
        }

        // 4) 更新 highWatermark（无论触不触发）
        double newHwm = Math.max(hwm, currentPrice);
        alert.setHighWatermark(newHwm);

        // 5) 创新高 → 重新武装
        if (currentPrice > hwm) {
            alert.setArmed(true);
        }

        alert.setLastEvaluatedAt(LocalDateTime.now());
        return AlertEvaluator.notTriggered();
    }
}
