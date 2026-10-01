package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.NewsEvent;
import tools.jackson.core.type.TypeReference;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;

/**
 * 资讯事件响应（spec §6 的分析字段 + spec §7 的落库字段，字段名一字不改）。
 *
 * <p>{@code risks} / {@code opportunities} / {@code relatedSymbols} 在库里是 JSON
 * **字符串**（TEXT 列，spec §7 明确要求），到响应里才反序列化成数组 ——
 * 前端只该看到数组，不该自己 parse 一次 JSON（那是把落库格式漏到接口契约里）。
 *
 * <p>{@code analyzed} 是额外字段（Python 侧也有同名标记）：含义是
 * "这条已经有解读了"。它由 {@code plainSummary} 是否有值推导，不是独立存储的状态 ——
 * 两个真相源迟早会不一致。
 */
public class NewsEventResponse {

    private Long id;
    /** 归一化代码（如 SH600519）；宏观资讯为 null */
    private String symbol;
    private String name;
    private String title;
    /** 摘要/正文（公告无正文时等于标题） */
    private String content;
    private String url;
    /** 1公告 2媒体 3研报 4舆情 */
    private Integer sourceLevel;
    private String sourceName;
    /** 源站自带分类（公告类型 / 财新 tag） */
    private String eventTypeRaw;
    private String eventType;
    /** 利好/利空/中性；**null 表示"信息不足，不判断方向"**（不是中性） */
    private String direction;
    private Double confidence;
    private String impactLevel;
    private List<String> relatedSymbols;
    private String plainSummary;
    private List<String> risks;
    private List<String> opportunities;
    /** 是否已经有过解读（= plainSummary 有值）；未解读时前端显示"信息不足，不判断方向" */
    private boolean analyzed;

    // ===== 可信度（Python news_credibility 规则引擎产出，静态属性，随分析落库）=====
    /** 0-100；null = 未评估 */
    private Integer credibility;
    /** 高 / 较高 / 中 / 较低 / 低 */
    private String credibilityGrade;
    /** 人话理由（"官方公告渠道""含传闻类措辞""另有信源印证"…），给"为什么可信/存疑" */
    private List<String> credibilityReasons;
    /** ⚠️ 传闻特征明显（措辞命中且非公告渠道）——展示层必须显著警示 */
    private boolean rumorFlag;
    /** 标题情绪化用词 */
    private boolean sensationalFlag;
    /** 批内交叉印证：另有不同信源讲述相似事件 */
    private boolean corroborated;

    // ===== 时效性（相对"现在"的属性，不落库、每次响应实时计算）=====
    /**
     * 距发布多少小时（向下取整）；发布时间缺失时为 null。
     * 新鲜度是相对量——冻结在分析时刻的"3 小时前"第二天就变成谎言，
     * 所以这一族字段永远在响应时现算（CnTime 口径，东八区）。
     */
    private Long freshHours;
    /** "3小时内" / "24小时内" / "3天前" / "3周前"；时间未知为 null */
    private String freshnessLabel;

    private LocalDateTime publishedAt;
    private LocalDateTime createdAt;

    public NewsEventResponse() {}

    /** 实体转响应。读 JSON 列需要一个 mapper，所以不像 {@code MessageResponse.from} 那样是单参方法。 */
    public static NewsEventResponse from(NewsEvent event, ObjectMapper mapper) {
        NewsEventResponse response = new NewsEventResponse();
        response.id = event.getId();
        response.symbol = event.getSymbol();
        response.name = event.getName();
        response.title = event.getTitle();
        response.content = event.getContent();
        response.url = event.getUrl();
        response.sourceLevel = event.getSourceLevel();
        response.sourceName = event.getSourceName();
        response.eventTypeRaw = event.getEventTypeRaw();
        response.eventType = event.getEventType();
        response.direction = event.getDirection();
        response.confidence = event.getConfidence();
        response.impactLevel = event.getImpactLevel();
        response.relatedSymbols = readStringList(event.getRelatedSymbols(), mapper);
        response.plainSummary = event.getPlainSummary();
        response.risks = readStringList(event.getRisks(), mapper);
        response.opportunities = readStringList(event.getOpportunities(), mapper);
        response.analyzed = event.getPlainSummary() != null && !event.getPlainSummary().isBlank();
        response.credibility = event.getCredibility();
        response.credibilityGrade = event.getCredibilityGrade();
        readCredibilityDetail(event.getCredibilityDetail(), mapper, response);
        response.freshHours = freshHours(event.getPublishedAt());
        response.freshnessLabel = freshnessLabel(event.getPublishedAt());
        response.publishedAt = event.getPublishedAt();
        response.createdAt = event.getCreatedAt();
        return response;
    }

    /**
     * 时效性分档。档位不追求精确（精确交给 freshHours），追求一眼可读：
     * "24小时内"和"27小时前"对决策是同一件事，不必让用户做减法。
     */
    static String freshnessLabel(LocalDateTime publishedAt) {
        if (publishedAt == null) {
            return null;
        }
        long hours = freshHours(publishedAt);
        if (hours < 1) return "1小时内";
        if (hours < 3) return "3小时内";
        if (hours < 6) return "6小时内";
        if (hours < 24) return "24小时内";
        long days = hours / 24;
        if (days < 7) return days + "天前";
        if (days < 30) return (days / 7) + "周前";
        return "30天前";
    }

    /** 距发布几小时；负数（时钟偏差/未来时间）按 0 计，时间未知返回 null。 */
    static Long freshHours(LocalDateTime publishedAt) {
        if (publishedAt == null) {
            return null;
        }
        return Math.max(0L, java.time.Duration.between(
                publishedAt, com.happyericsix.stocktracker.util.CnTime.now()).toHours());
    }

    /** 解析 credibility_detail JSON；坏数据退化成空值，绝不让一条明细拖垮整个列表。 */
    private static void readCredibilityDetail(String json, ObjectMapper mapper, NewsEventResponse response) {
        if (json == null || json.isBlank()) {
            return;
        }
        try {
            var detail = mapper.readValue(json,
                    new TypeReference<java.util.Map<String, Object>>() {});
            Object reasons = detail.get("reasons");
            if (reasons instanceof List<?> list) {
                response.credibilityReasons = list.stream()
                        .map(String::valueOf).filter(s -> !s.isBlank()).toList();
            }
            response.rumorFlag = Boolean.TRUE.equals(detail.get("rumor_flag"));
            response.sensationalFlag = Boolean.TRUE.equals(detail.get("sensational_flag"));
            response.corroborated = Boolean.TRUE.equals(detail.get("corroborated"));
        } catch (Exception ignored) {
            // 明细是增强件：解析失败只是少几个徽章，不该让响应 500
        }
    }

    /** JSON 字符串数组 → List；坏数据不该让整个搜索接口 500，所以失败就退化成空列表。 */
    private static List<String> readStringList(String json, ObjectMapper mapper) {
        if (json == null || json.isBlank()) {
            return List.of();
        }
        try {
            List<String> values = mapper.readValue(json, new TypeReference<List<String>>() {});
            return values == null ? List.of() : values;
        } catch (Exception e) {
            return List.of();
        }
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public String getName() { return name; }
    public void setName(String name) { this.name = name; }
    public String getTitle() { return title; }
    public void setTitle(String title) { this.title = title; }
    public String getContent() { return content; }
    public void setContent(String content) { this.content = content; }
    public String getUrl() { return url; }
    public void setUrl(String url) { this.url = url; }
    public Integer getSourceLevel() { return sourceLevel; }
    public void setSourceLevel(Integer sourceLevel) { this.sourceLevel = sourceLevel; }
    public String getSourceName() { return sourceName; }
    public void setSourceName(String sourceName) { this.sourceName = sourceName; }
    public String getEventTypeRaw() { return eventTypeRaw; }
    public void setEventTypeRaw(String eventTypeRaw) { this.eventTypeRaw = eventTypeRaw; }
    public String getEventType() { return eventType; }
    public void setEventType(String eventType) { this.eventType = eventType; }
    public String getDirection() { return direction; }
    public void setDirection(String direction) { this.direction = direction; }
    public Double getConfidence() { return confidence; }
    public void setConfidence(Double confidence) { this.confidence = confidence; }
    public String getImpactLevel() { return impactLevel; }
    public void setImpactLevel(String impactLevel) { this.impactLevel = impactLevel; }
    public List<String> getRelatedSymbols() { return relatedSymbols; }
    public void setRelatedSymbols(List<String> relatedSymbols) { this.relatedSymbols = relatedSymbols; }
    public String getPlainSummary() { return plainSummary; }
    public void setPlainSummary(String plainSummary) { this.plainSummary = plainSummary; }
    public List<String> getRisks() { return risks; }
    public void setRisks(List<String> risks) { this.risks = risks; }
    public List<String> getOpportunities() { return opportunities; }
    public void setOpportunities(List<String> opportunities) { this.opportunities = opportunities; }
    public boolean isAnalyzed() { return analyzed; }
    public void setAnalyzed(boolean analyzed) { this.analyzed = analyzed; }
    public Integer getCredibility() { return credibility; }
    public void setCredibility(Integer credibility) { this.credibility = credibility; }
    public String getCredibilityGrade() { return credibilityGrade; }
    public void setCredibilityGrade(String credibilityGrade) { this.credibilityGrade = credibilityGrade; }
    public List<String> getCredibilityReasons() { return credibilityReasons; }
    public void setCredibilityReasons(List<String> credibilityReasons) { this.credibilityReasons = credibilityReasons; }
    public boolean isRumorFlag() { return rumorFlag; }
    public void setRumorFlag(boolean rumorFlag) { this.rumorFlag = rumorFlag; }
    public boolean isSensationalFlag() { return sensationalFlag; }
    public void setSensationalFlag(boolean sensationalFlag) { this.sensationalFlag = sensationalFlag; }
    public boolean isCorroborated() { return corroborated; }
    public void setCorroborated(boolean corroborated) { this.corroborated = corroborated; }
    public Long getFreshHours() { return freshHours; }
    public void setFreshHours(Long freshHours) { this.freshHours = freshHours; }
    public String getFreshnessLabel() { return freshnessLabel; }
    public void setFreshnessLabel(String freshnessLabel) { this.freshnessLabel = freshnessLabel; }
    public LocalDateTime getPublishedAt() { return publishedAt; }
    public void setPublishedAt(LocalDateTime publishedAt) { this.publishedAt = publishedAt; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
