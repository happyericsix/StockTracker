package com.happyericsix.stocktracker.dto;

import com.fasterxml.jackson.annotation.JsonAlias;
import lombok.Data;

import java.time.LocalDateTime;

/**
 * 一条待写入的语义事实。
 *
 * <p>注意 {@code object} 这个名字：在 API/JSON 与 Python 侧保持 subject-predicate-object
 * 这套熟悉的表达，落到实体时映射为 {@code factValue}（避免 object 这个在 HQL 里有歧义的词）。
 *
 * <p>{@code @JsonAlias} 是刻意加的宽容度：Python 侧的内部结构习惯用 snake_case，
 * 一旦哪天映射漏了，{@code fact_type} 会静默变成默认的 observation、
 * {@code event_time} 会静默丢掉 —— 而时间是这套记忆系统的核心，丢了不会报错、
 * 只会让"去年买的时候"退化成"不知道什么时候"。类型别名让这类漂移不致命。
 */
@Data
public class MemoryFactRequest {

    /** 主体：user / 600519 / strategy:MA-cross */
    private String subject;

    /** 谓词：risk_preference / stop_loss_pct / holding_cost */
    private String predicate;

    /** 值 */
    private String object;

    /** preference / constraint / goal / holding / decision / observation */
    @JsonAlias("fact_type")
    private String factType;

    private Double confidence;

    /** 内容所指时间（由 Python 侧把"去年"这类相对时间解析成绝对时间后传入） */
    @JsonAlias("event_time")
    private LocalDateTime eventTime;

    /** 原始时间措辞（"去年""上周三"）——必须保留，否则将来答不了"我什么时候说的" */
    @JsonAlias("raw_time_phrase")
    private String rawTimePhrase;

    /** 数据口径时间：行情/财报类事实必填 */
    @JsonAlias("data_as_of")
    private LocalDateTime dataAsOf;

    /** user / model / tool / external */
    private String provenance;

    /** high / medium / low */
    private String trust;

    /** 用户是否确认过；模型推断的一律 false */
    private Boolean confirmed;
}
