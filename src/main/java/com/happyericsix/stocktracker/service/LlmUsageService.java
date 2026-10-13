package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.MemoryEvent;
import com.happyericsix.stocktracker.repository.MemoryEventRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * LLM 用量看板：把账本里 `kind=usage` 的事件聚成"每天花了多少"。
 *
 * <h3>为什么必须有这个</h3>
 * 委员会（每策略每天 ~8 次调用）+ 资讯解读 + 记忆整合都是 LLM 往返，
 * 用户量涨 = 成本线性涨，而现在没有任何地方能回答"这个月花了多少 token"。
 * 计量数据 Python 侧早就落账本了（`metering._queue_usage`，一行 SQL 的事），
 * 这里只是把它变成人能看的形状。
 *
 * <p>预算硬顶在 Python 侧（`LLM_DAILY_TOKEN_BUDGET` 环境变量，进程内计数，
 * 重启清零——够用的第一版：预算是"止损线"不是"账单"）。
 */
@Service
public class LlmUsageService {
    private static final Logger log = LoggerFactory.getLogger(LlmUsageService.class);

    private final MemoryEventRepository memoryEventRepository;
    private final ObjectMapper mapper = new ObjectMapper();

    /** 与 Python 侧 LLM_DAILY_TOKEN_BUDGET 同源的人类可读展示（真正的执行在 Python）。 */
    @Value("${llm.daily-token-budget:0}")
    private long dailyTokenBudget;

    public LlmUsageService(MemoryEventRepository memoryEventRepository) {
        this.memoryEventRepository = memoryEventRepository;
    }

    public Map<String, Object> usage(int days) {
        int window = Math.max(1, Math.min(days, 90));
        LocalDateTime since = LocalDateTime.now().minusDays(window);
        List<MemoryEvent> events = List.of();
        try {
            events = memoryEventRepository
                    .findByKindAndIngestedAtGreaterThanEqualOrderByIngestedAtAsc("usage", since);
        } catch (Exception e) {
            log.warn("usage events load failed: {}", e.getMessage());
        }

        // 按天聚合：day → [llmCalls, llmTokens, toolCalls]
        Map<LocalDate, long[]> byDay = new LinkedHashMap<>();
        for (MemoryEvent e : events) {
            long[] acc = byDay.computeIfAbsent(
                    e.getIngestedAt() == null ? LocalDate.now() : e.getIngestedAt().toLocalDate(),
                    k -> new long[3]);
            applyDelta(acc, e.getMeta());
        }

        long calls = 0, tokens = 0, toolCalls = 0;
        List<Map<String, Object>> series = new ArrayList<>();
        for (Map.Entry<LocalDate, long[]> e : byDay.entrySet()) {
            calls += e.getValue()[0];
            tokens += e.getValue()[1];
            toolCalls += e.getValue()[2];
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("date", e.getKey().toString());
            row.put("llmCalls", e.getValue()[0]);
            row.put("llmTokens", e.getValue()[1]);
            row.put("toolCalls", e.getValue()[2]);
            series.add(row);
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("days", window);
        out.put("dailyTokenBudget", dailyTokenBudget);
        out.put("totals", Map.of(
                "llmCalls", calls,
                "llmTokens", tokens,
                "toolCalls", toolCalls,
                "usageEvents", events.size()));
        out.put("byDay", series);
        return out;
    }

    /** meta 形如 {"delta":{"llm.calls":2,"llm.total_tokens":3500,"tools.calls":1},...}。 */
    private void applyDelta(long[] acc, String meta) {
        if (meta == null || meta.isBlank()) {
            return;
        }
        try {
            JsonNode root = mapper.readTree(meta);
            JsonNode delta = root.get("delta");
            if (delta == null) {
                return;
            }
            acc[0] += delta.path("llm.calls").asLong(0);
            acc[1] += delta.path("llm.total_tokens").asLong(0);
            acc[2] += delta.path("tools.calls").asLong(0);
        } catch (Exception e) {
            // 脏 meta 跳过：一行坏数据不该让整个看板挂掉
            log.debug("bad usage meta: {}", e.getMessage());
        }
    }
}
