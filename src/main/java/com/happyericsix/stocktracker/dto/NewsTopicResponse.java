package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.NewsTopic;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * 事件线（新闻专题）响应：雷达页"事件线"区块 + 专题详情共用。
 *
 * <p>{@code summary == null} 表示还没撰稿 —— 前端显示成员列表与
 * {@code skipReason} 对应的文案（"AI 综合暂缺"），而不是把专题藏起来：
 * 事件线本身（标题 + 成员）没有 LLM 也成立。
 */
public record NewsTopicResponse(
        Long id,
        String title,
        String digest,
        String summary,
        String outlook,
        String direction,
        Double confidence,
        List<String> symbols,
        List<String> sources,
        int eventCount,
        int sourceCount,
        String status,
        String skipReason,
        String editorReason,
        boolean synthesized,
        LocalDateTime firstEventAt,
        LocalDateTime lastEventAt) {

    public static NewsTopicResponse from(NewsTopic topic, ObjectMapper mapper) {
        return new NewsTopicResponse(
                topic.getId(),
                topic.getTitle(),
                topic.getDigest(),
                topic.getSummary(),
                topic.getOutlook(),
                topic.getDirection(),
                topic.getConfidence(),
                stringList(readTree(mapper, topic.getSymbols())),
                stringList(readTree(mapper, topic.getSources())),
                topic.getEventCount() == null ? 0 : topic.getEventCount(),
                topic.getSourceCount() == null ? 0 : topic.getSourceCount(),
                topic.getStatus(),
                topic.getSkipReason(),
                topic.getEditorReason(),
                topic.getSynthesizedAt() != null,
                topic.getFirstEventAt(),
                topic.getLastEventAt());
    }

    private static JsonNode readTree(ObjectMapper mapper, String json) {
        if (json == null || json.isBlank()) {
            return null;
        }
        try {
            return mapper.readTree(json);
        } catch (Exception e) {
            return null;
        }
    }

    private static List<String> stringList(JsonNode node) {
        if (node == null || !node.isArray()) {
            return List.of();
        }
        List<String> values = new ArrayList<>();
        for (JsonNode element : node) {
            if (element != null && !element.isNull()) {
                String text = element.asText("").trim();
                if (!text.isEmpty()) {
                    values.add(text);
                }
            }
        }
        return values;
    }
}
