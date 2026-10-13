package com.happyericsix.stocktracker.service;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * 记忆展示标签（2026-10-06 线上反馈"记忆页看不懂"后补的唯一真相源）。
 *
 * 用户在记忆页看到的必须是"止损 5%""策略 #3 回测总收益 12.5%"，
 * 而不是取代链的稳定键原文（stop_loss_pct / strategy:3）。
 */
class MemoryDisplayLabelsTest {

    @Test
    void userPreferencePredicatesTranslateToChinese() {
        assertEquals("止损", MemoryDisplayLabels.predicateLabel("stop_loss_pct"));
        assertEquals("关注理由", MemoryDisplayLabels.predicateLabel("watch_reason"));
    }

    /** 客观事实谓词此前完全没有翻译，是"看不懂"的主要来源 */
    @Test
    void objectivePredicatesTranslateToChinese() {
        assertEquals("回测总收益", MemoryDisplayLabels.predicateLabel("backtest_total_return_pct"));
        assertEquals("模拟盘净值", MemoryDisplayLabels.predicateLabel("paper_equity"));
        assertEquals("验证平均超额收益", MemoryDisplayLabels.predicateLabel("verify_avg_excess_pct"));
        assertEquals("预期到期日", MemoryDisplayLabels.predicateLabel("expectation_deadline"));
    }

    /** 未登记的键显示原文而不是"未知"：宁可英文也不能丢信息 */
    @Test
    void unknownPredicateFallsBackToRawKey() {
        assertEquals("some_new_predicate", MemoryDisplayLabels.predicateLabel("some_new_predicate"));
        assertEquals("", MemoryDisplayLabels.predicateLabel(null));
    }

    @Test
    void subjectLabelHidesUserAndHumanizesStrategy() {
        assertNull(MemoryDisplayLabels.subjectLabel("user"));
        assertNull(MemoryDisplayLabels.subjectLabel("USER"));
        assertNull(MemoryDisplayLabels.subjectLabel(null));
        assertEquals("策略 #3", MemoryDisplayLabels.subjectLabel("strategy:3"));
        // 股票代码保持原文：证券语境下 600519 本身就是通用表达
        assertEquals("600519", MemoryDisplayLabels.subjectLabel("600519"));
    }

    @Test
    void percentPredicatesGetUnitSuffix() {
        assertEquals("12.5%", MemoryDisplayLabels.objectLabel("backtest_total_return_pct", "12.5"));
        assertEquals("-8%", MemoryDisplayLabels.objectLabel("stop_loss_pct", "-8"));
        // 非数值（解析失败/文本）不硬加单位
        assertEquals("稳健", MemoryDisplayLabels.objectLabel("risk_preference", "稳健"));
        assertEquals("high", MemoryDisplayLabels.objectLabel("paper_return_pct", "high"));
    }

    @Test
    void expectationStatusValuesTranslate() {
        assertEquals("已达成", MemoryDisplayLabels.objectLabel("expectation_status", "met"));
        assertEquals("待验证", MemoryDisplayLabels.objectLabel("expectation_status", "pending"));
        assertEquals("无法度量", MemoryDisplayLabels.objectLabel("expectation_status", "unmeasurable"));
        // 同样的文本在别的谓词上不翻译（met 只是预期状态的枚举值）
        assertEquals("met", MemoryDisplayLabels.objectLabel("expectation_metric", "met"));
    }
}
