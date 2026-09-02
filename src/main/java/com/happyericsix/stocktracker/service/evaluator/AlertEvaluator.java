package com.happyericsix.stocktracker.service.evaluator;

import com.happyericsix.stocktracker.dto.RefreshedPrice;
import com.happyericsix.stocktracker.entity.Alert;

/**
 * 预警评估器接口：每个 conditionType 一个实现
 *
 * 评估方法返回 EvaluationResult 包含：
 *  - triggered: 是否触发
 *  - triggerValue: 触发时的指标值（用于展示 "RSI 升至 72.5"）
 *  - reason: 不可用原因（数据缺失/买入价缺失等），日志用
 *
 * 设计要点：评估器只接 RefreshedPrice，不依赖 StockService / Cache，
 * 由调用方（Listener）保证"价就是刚拉的那一份"，避免时点不一致
 */
public interface AlertEvaluator {

    /** 支持的 conditionType，与前端 Alerts.vue 的 conditionTypes 一致 */
    String supportedType();

    /** 评估是否触发；数据不足返回 notTriggered("reason") */
    EvaluationResult evaluate(Alert alert, RefreshedPrice data);

    /** 便捷：未触发 */
    static EvaluationResult notTriggered() {
        return new EvaluationResult(false, null, null);
    }

    /** 便捷：未触发 + 原因 */
    static EvaluationResult notTriggered(String reason) {
        return new EvaluationResult(false, null, reason);
    }

    /** 便捷：触发 + 指标值 */
    static EvaluationResult triggered(double triggerValue) {
        return new EvaluationResult(true, triggerValue, null);
    }
}
