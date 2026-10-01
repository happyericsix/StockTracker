package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.data.domain.Pageable;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.when;

/**
 * 训练数据导出（内部接口）的契约：**fail-closed 鉴权** + 只出已解读条目。
 *
 * <p>这个接口能导出全局资讯事实表，鉴权语义必须与 InternalMemoryController
 * 完全一致——内部接口的口子松一处，"内部"就名存实亡。
 */
@ExtendWith(MockitoExtension.class)
class InternalNewsControllerTest {

    @Mock
    private NewsEventRepository newsEventRepository;

    @Test
    void rejectsWhenTokenIsNotConfigured() {
        InternalNewsController bare = new InternalNewsController(newsEventRepository, "");

        assertEquals(HttpStatus.UNAUTHORIZED, bare.trainingExport(30, 100, "anything").getStatusCode());
    }

    @Test
    void rejectsWrongTokenEvenWhenOneIsConfigured() {
        InternalNewsController secured = new InternalNewsController(newsEventRepository, "right-token");

        assertEquals(HttpStatus.UNAUTHORIZED, secured.trainingExport(30, 100, "wrong-token").getStatusCode());
        assertEquals(HttpStatus.UNAUTHORIZED, secured.trainingExport(30, 100, null).getStatusCode());
    }

    @Test
    @SuppressWarnings("unchecked")
    void exportsOnlyAnalyzedRowsWithClippedContent() {
        // @InjectMocks 不会处理 @Value（令牌为空 → fail-closed），
        // 走授权路径的用例必须显式带令牌构造
        InternalNewsController secured = new InternalNewsController(newsEventRepository, "right-token");
        NewsEvent analyzed = NewsEvent.builder()
                .id(1L).symbol("SH600519").title("回购公告")
                .content("x".repeat(2000))
                .sourceLevel(1).sourceName("上交所")
                .publishedAt(LocalDateTime.of(2026, 9, 18, 8, 0))
                .eventType("回购").direction("利好").confidence(0.8)
                .impactLevel("medium").plainSummary("回购注销，利好每股指标")
                .credibility(95).credibilityGrade("高")
                .build();
        when(newsEventRepository.findByPlainSummaryNotNullAndPublishedAtGreaterThanEqualOrderByPublishedAtDesc(
                any(LocalDateTime.class), any(Pageable.class))).thenReturn(List.of(analyzed));

        ResponseEntity<Map<String, Object>> response =
                secured.trainingExport(30, 100, "right-token");

        assertEquals(HttpStatus.OK, response.getStatusCode());
        List<Map<String, Object>> items = (List<Map<String, Object>>) response.getBody().get("items");
        assertEquals(1, items.size());
        Map<String, Object> row = items.get(0);
        // 正文截断在导出侧做一次：训练样本不需要整篇公告
        assertEquals(800, ((String) row.get("content")).length());
        Map<String, Object> analysis = (Map<String, Object>) row.get("analysis");
        assertEquals("利好", analysis.get("direction"));
        assertEquals(95, row.get("credibility"));
        assertFalse(((String) row.get("title")).isEmpty());
        assertTrue(row.containsKey("source_level"));
    }
}
