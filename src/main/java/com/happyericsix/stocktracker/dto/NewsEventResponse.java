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
        response.publishedAt = event.getPublishedAt();
        response.createdAt = event.getCreatedAt();
        return response;
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
    public LocalDateTime getPublishedAt() { return publishedAt; }
    public void setPublishedAt(LocalDateTime publishedAt) { this.publishedAt = publishedAt; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
