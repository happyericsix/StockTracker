package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.data.domain.Sort;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 资讯数据的<b>内部导出接口</b>：给 Python 侧的领域模型微调管线
 * （{@code python-data-service/finetune/}）取训练语料，不面向前端。
 *
 * <h3>为什么走接口而不是让 Python 直连 MySQL</h3>
 * 与 {@link InternalMemoryController} 同一条纪律：数据库凭据只存在于 Java 进程，
 * Python 能取到什么被接口形状限制死。训练语料是<b>只读快照</b>，
 * 导出时顺带做两件 Python 侧不该各自重复实现的事：
 * <ul>
 *   <li><b>只出"已解读"的条目</b>（plainSummary 非空）——蒸馏的监督信号来自
 *       已通过 schema 规整的分析，而不是降级行；</li>
 *   <li><b>正文截断</b>（训练样本不需要整篇公告，截断在导出侧做一次，
 *       数据集构建侧就不会各截各的）。</li>
 * </ul>
 *
 * <h3>鉴权</h3>
 * 与 {@code require_internal_token} 中间件对称：共享密钥放在 {@code X-Internal-Token}
 * 头里，常量时间比较；未配置密钥一律拒绝（fail closed）。这里能导出的是
 * 全局资讯事实（不含用户身份数据），但仍与记忆接口同级保护——
 * 内部接口的口子一旦松一处，"内部"就名存实亡。
 */
@RestController
@RequestMapping("/api/v1/internal/news")
public class InternalNewsController {

    private static final Logger log = LoggerFactory.getLogger(InternalNewsController.class);

    /** 训练样本的正文截断长度：长文对蒸馏任务是噪声，短样本让 batch 更均匀。 */
    private static final int TRAINING_CONTENT_CLIP = 800;

    /** 单次导出上限：防手滑把 limit 拉到百万级拖垮库。 */
    private static final int MAX_LIMIT = 5000;

    private final NewsEventRepository newsEventRepository;
    private final String internalToken;

    public InternalNewsController(NewsEventRepository newsEventRepository,
                                  @Value("${internal.api-token:}") String internalToken) {
        this.newsEventRepository = newsEventRepository;
        this.internalToken = internalToken;
    }

    /**
     * 导出"已解读"资讯（含分析字段与可信度标注）作为微调语料。
     * 按发布时间倒序取最近 {@code days} 天，最多 {@code limit} 条。
     */
    @GetMapping("/training-export")
    public ResponseEntity<Map<String, Object>> trainingExport(
            @RequestParam(required = false, defaultValue = "180") int days,
            @RequestParam(required = false, defaultValue = "2000") int limit,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        int safeDays = Math.max(1, Math.min(days, 3650));
        int safeLimit = Math.max(1, Math.min(limit, MAX_LIMIT));
        LocalDateTime since = CnTime.today().minusDays(safeDays).atStartOfDay();

        List<NewsEvent> events = newsEventRepository
                .findByPlainSummaryNotNullAndPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                        since, PageRequest.of(0, safeLimit));

        List<Map<String, Object>> rows = new ArrayList<>();
        for (NewsEvent event : events) {
            rows.add(trainingRow(event));
        }
        log.info("internal news training-export: days={}, limit={}, returned={}",
                safeDays, safeLimit, rows.size());
        return ResponseEntity.ok(Map.of(
                "items", rows,
                "count", rows.size(),
                "since", since.toString()));
    }

    private Map<String, Object> trainingRow(NewsEvent event) {
        Map<String, Object> row = new LinkedHashMap<>();
        row.put("id", event.getId());
        row.put("symbol", event.getSymbol());
        row.put("title", event.getTitle());
        row.put("content", clip(event.getContent(), TRAINING_CONTENT_CLIP));
        row.put("source_level", event.getSourceLevel());
        row.put("source_name", event.getSourceName());
        row.put("published_at", event.getPublishedAt() == null ? null : event.getPublishedAt().toString());
        // 蒸馏目标：已规整的分析结论（normalize_analysis 的输出形状，spec §6）
        row.put("analysis", Map.of(
                "event_type", nullToEmpty(event.getEventType()),
                "direction", nullToEmpty(event.getDirection()),
                "confidence", event.getConfidence() == null ? 0.0 : event.getConfidence(),
                "impact_level", nullToEmpty(event.getImpactLevel()),
                "plain_summary", nullToEmpty(event.getPlainSummary())));
        // 可信度标注（规则引擎产物）：给"传闻识别"辅助任务的弱监督标签
        row.put("credibility", event.getCredibility());
        row.put("credibility_grade", nullToEmpty(event.getCredibilityGrade()));
        return row;
    }

    private static String nullToEmpty(String value) {
        return value == null ? "" : value;
    }

    private static String clip(String value, int max) {
        if (value == null) {
            return "";
        }
        return value.length() <= max ? value : value.substring(0, max);
    }

    private boolean authorized(String token) {
        if (internalToken == null || internalToken.isBlank()) {
            log.error("internal.api-token 未配置，拒绝内部资讯导出接口访问");
            return false;
        }
        if (token == null) {
            return false;
        }
        return MessageDigest.isEqual(token.getBytes(StandardCharsets.UTF_8),
                internalToken.getBytes(StandardCharsets.UTF_8));
    }

    private <T> ResponseEntity<T> unauthorized() {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).build();
    }
}
