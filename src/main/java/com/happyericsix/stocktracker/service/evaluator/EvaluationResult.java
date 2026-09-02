package com.happyericsix.stocktracker.service.evaluator;

/**
 * 评估结果：不可变 record
 */
public record EvaluationResult(boolean triggered, Double triggerValue, String reason) {
    public boolean isTriggered() { return triggered; }
}
