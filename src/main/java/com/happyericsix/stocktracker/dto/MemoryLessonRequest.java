package com.happyericsix.stocktracker.dto;

import lombok.Data;

import java.util.List;

/**
 * 一条待写入的经验（"这类问题上次是怎么解决的"）。
 *
 * <p>字段刻意分成 symptom / attempts / resolution / reusable_rule 四段，
 * 而不是一段自由文本：这样注入时能只给模型看"症状 + 做法"这两段，
 * 而且"可复用做法"必须写成一句可执行、可验证的话，不能是投资结论。
 */
@Data
public class MemoryLessonRequest {

    /** 任务类型：generate_strategy / backtest / quote_lookup */
    private String taskType;

    /** 症状：可复现的失败现象 */
    private String symptom;

    /** 上下文（股票、参数），自由结构 */
    private Object context;

    /** 试过什么、为什么不行 */
    private List<String> attempts;

    /** 最后是怎么解决的 */
    private String resolution;

    /** 可复用做法（一句可执行的话） */
    private String reusableRule;

    private Double confidence;

    /** user / model / tool / external */
    private String provenance;

    /** high / medium / low */
    private String trust;
}
