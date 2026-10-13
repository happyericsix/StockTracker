package com.happyericsix.stocktracker.service;

import java.util.List;
import java.util.Set;

/**
 * 新闻事件线契约：Java 与 Python（python-data-service/news_topics.py）共用的封闭集。
 *
 * <p>改任何值必须两侧同步改，{@code tests/test_news_topic_contract_java.py}
 * 会逐字比对 —— 这套纪律来自 ExecutionContract 的教训：两边各写一个
 * "看起来一样"的字符串，表现是"统计不到一起/判定永远不等"，不报错、只是悄悄不对。
 *
 * <p>范围：撰稿/总编输出的字段名、方向枚举、事件线未生成综合的原因枚举。
 */
public final class NewsTopicContract {

    private NewsTopicContract() {
    }

    // >>> NEWS_TOPIC_CONTRACT
    public static final String DIRECTION_BULLISH = "利好";
    public static final String DIRECTION_BEARISH = "利空";
    public static final String DIRECTION_NEUTRAL = "中性";

    /** 事件线未生成综合的原因（Python 侧 news_topics.SKIP_REASONS，逐字一致） */
    public static final String SKIP_EDITOR_LLM_UNAVAILABLE = "editor_llm_unavailable";
    public static final String SKIP_EDITOR_BUDGET_EXCEEDED = "editor_budget_exceeded";
    public static final String SKIP_EDITOR_COOLDOWN = "editor_cooldown";
    public static final String SKIP_EDITOR_LOW_PRIORITY = "editor_skip_low_priority";
    // <<< NEWS_TOPIC_CONTRACT

    /** 方向封闭集（展示与统计共用） */
    public static final List<String> DIRECTIONS = List.of(
            DIRECTION_BULLISH, DIRECTION_BEARISH, DIRECTION_NEUTRAL);

    /** 未撰稿原因封闭集（落库 news_topics.skip_reason，前端按它显示文案） */
    public static final List<String> SKIP_REASONS = List.of(
            SKIP_EDITOR_LLM_UNAVAILABLE, SKIP_EDITOR_BUDGET_EXCEEDED,
            SKIP_EDITOR_COOLDOWN, SKIP_EDITOR_LOW_PRIORITY);

    /** 事件线状态：open=仍在进展，closed=超过静默窗口没有新报道 */
    public static final String STATUS_OPEN = "open";
    public static final String STATUS_CLOSED = "closed";

    /** 撰稿输出的字段名（Python news_topics.WRITER_OUTPUT_FIELDS，逐字一致；digest 是 30 字内一句话结论） */
    public static final Set<String> WRITER_OUTPUT_FIELDS = Set.of(
            "digest", "title", "story", "outlook", "direction", "confidence", "affected_symbols");

    /** 总编输出的字段名（Python news_topics.EDITOR_OUTPUT_FIELDS，逐字一致） */
    public static final Set<String> EDITOR_OUTPUT_FIELDS = Set.of(
            "index", "title", "worth_deep", "reason");
}
