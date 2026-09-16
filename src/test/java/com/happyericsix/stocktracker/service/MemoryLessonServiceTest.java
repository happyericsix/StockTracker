package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryLessonRequest;
import com.happyericsix.stocktracker.dto.MemoryLessonsRequest;
import com.happyericsix.stocktracker.entity.MemoryLesson;
import com.happyericsix.stocktracker.repository.MemoryLessonRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.*;

/**
 * 经验记忆（"这类问题上次是怎么解决的"）。
 *
 * 三条行为必须钉住：
 * 1. 新经验一律 <b>pending</b>（人工确认才能变可信，防轨迹投毒）；
 * 2. 同一症状重复出现 = 追加一行，行数即"遇到过几次"（复发次数是升格 skill 的判据）；
 * 3. 检索只给"每个键最新一条"，且排除已停用的。
 */
@ExtendWith(MockitoExtension.class)
class MemoryLessonServiceTest {

    @Mock
    private MemoryLessonRepository lessonRepo;

    private MemoryLessonService service;

    @BeforeEach
    void setUp() {
        service = new MemoryLessonService(lessonRepo, new ObjectMapper());
    }

    private MemoryLessonRequest lesson(String taskType, String symptom, String resolution) {
        MemoryLessonRequest request = new MemoryLessonRequest();
        request.setTaskType(taskType);
        request.setSymptom(symptom);
        request.setResolution(resolution);
        request.setReusableRule("回测前先确认历史条数≥20");
        request.setConfidence(0.8);
        return request;
    }

    private MemoryLessonsRequest batch(MemoryLessonRequest... lessons) {
        MemoryLessonsRequest request = new MemoryLessonsRequest();
        request.setUserId(1L);
        request.setSessionKey("1:2026-09-16");
        request.setEvidenceEventIds(List.of(11L, 12L));
        request.setLessons(List.of(lessons));
        return request;
    }

    private MemoryLesson stored(Long id, String taskType, String symptom, String status) {
        return MemoryLesson.builder()
                .id(id).userId(1L).taskType(taskType).symptom(symptom)
                .resolution("换更长周期的标的").reusableRule("先检查历史条数")
                .status(status).confidence(0.8).trust("medium")
                .recordedAt(LocalDateTime.of(2026, 9, 16, 10, 0))
                .build();
    }

    // ==================== 写入 ====================

    @Test
    void newLessonIsStoredAsPending() {
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(1L, "generate_strategy", "回测数据不足"))
                .thenReturn(1L);

        service.saveLessons(batch(lesson("Generate_Strategy", "回测数据不足", "改用更长周期的标的")));

        ArgumentCaptor<MemoryLesson> captor = ArgumentCaptor.forClass(MemoryLesson.class);
        verify(lessonRepo).save(captor.capture());
        // 人工确认之前，经验不是"可信做法"
        assertEquals(MemoryLesson.STATUS_PENDING, captor.getValue().getStatus());
        // 任务类型统一小写，否则键会分裂、复发次数统计不出来
        assertEquals("generate_strategy", captor.getValue().getTaskType());
        assertEquals("[11,12]", captor.getValue().getEvidenceEventIds());
    }

    @Test
    void lessonWithoutResolutionOrRuleIsDropped() {
        MemoryLessonRequest complaint = lesson("backtest", "回测很慢", null);
        complaint.setReusableRule(null);

        assertTrue(service.saveLessons(batch(complaint)).isEmpty());
        verify(lessonRepo, never()).save(any(MemoryLesson.class));
    }

    @Test
    void lowConfidenceLessonIsDropped() {
        MemoryLessonRequest unsure = lesson("backtest", "回测很慢", "换数据源");
        unsure.setConfidence(0.05);

        assertTrue(service.saveLessons(batch(unsure)).isEmpty());
    }

    @Test
    void repeatOfTheSameSymptomAppendsAndCountsOccurrences() {
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(1L, "backtest", "回测数据不足"))
                .thenReturn(3L);   // 这次是第 3 次遇到

        List<MemoryLessonService.LessonWriteResult> results =
                service.saveLessons(batch(lesson("backtest", "回测数据不足", "换标的")));

        // 行数就是复发次数：达到阈值会在日志里提示人工审核是否升格 skill，但不自动升格
        assertEquals(3L, results.get(0).occurrences());
        assertEquals("created", results.get(0).action());
    }

    @Test
    void batchIsCapped() {
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(any(), anyString(), anyString())).thenReturn(1L);

        MemoryLessonRequest[] many = new MemoryLessonRequest[MemoryLessonService.MAX_LESSONS_PER_BATCH + 4];
        for (int i = 0; i < many.length; i++) {
            many[i] = lesson("task" + i, "symptom" + i, "resolution");
        }
        assertEquals(MemoryLessonService.MAX_LESSONS_PER_BATCH, service.saveLessons(batch(many)).size());
    }

    // ==================== 审核 ====================

    @Test
    void activateAndRetireOnlyTouchTheOwnersLesson() {
        when(lessonRepo.findById(7L)).thenReturn(Optional.of(stored(7L, "backtest", "慢", MemoryLesson.STATUS_PENDING)));
        when(lessonRepo.findById(8L)).thenReturn(Optional.of(stored(8L, "backtest", "慢", MemoryLesson.STATUS_ACTIVE)));
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));

        assertTrue(service.activate(1L, 7L));
        assertTrue(service.retire(1L, 8L));
        // 别人的经验：既不能确认也不能停用
        when(lessonRepo.findById(9L)).thenReturn(Optional.of(MemoryLesson.builder().id(9L).userId(2L).build()));
        assertFalse(service.activate(1L, 9L));
    }

    @Test
    void activateReturnsFalseForMissingLesson() {
        when(lessonRepo.findById(99L)).thenReturn(Optional.empty());
        assertFalse(service.activate(1L, 99L));
    }

    // ==================== 检索 ====================

    @Test
    void searchReturnsOnlyTheLatestRowPerSymptom() {
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(
                stored(3L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE),   // 最新
                stored(2L, "backtest", "数据不足", MemoryLesson.STATUS_PENDING),
                stored(1L, "quote", "取不到行情", MemoryLesson.STATUS_PENDING)));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(1L, "backtest", "数据不足")).thenReturn(2L);
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(1L, "quote", "取不到行情")).thenReturn(1L);

        List<Map<String, Object>> found = service.searchLessons(1L, null, null, 5);

        assertEquals(2, found.size());
        Map<String, Object> backtest = found.stream()
                .filter(item -> "数据不足".equals(item.get("symptom"))).findFirst().orElseThrow();
        assertEquals(3L, backtest.get("id"));
        assertEquals("active", backtest.get("status"));
        assertEquals(2L, backtest.get("occurrences"));
        assertFalse((Boolean) backtest.get("promotionSuggested"));
    }

    @Test
    void retiredLessonsAreNeverInjected() {
        when(lessonRepo.findByUserIdOrderByIdDesc(1L))
                .thenReturn(List.of(stored(5L, "backtest", "数据不足", MemoryLesson.STATUS_RETIRED)));

        assertTrue(service.searchLessons(1L, null, null, 5).isEmpty());
    }

    @Test
    void recurrenceIsReportedAsAPromotionHint() {
        when(lessonRepo.findByUserIdOrderByIdDesc(1L))
                .thenReturn(List.of(stored(5L, "backtest", "数据不足", MemoryLesson.STATUS_PENDING)));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(1L, "backtest", "数据不足")).thenReturn(3L);

        List<Map<String, Object>> found = service.searchLessons(1L, null, null, 5);

        // 提示"可以考虑升格为 skill"，但决定权在人手里
        assertTrue((Boolean) found.get(0).get("promotionSuggested"));
    }

    @Test
    void activeLessonsOutrankPendingOnes() {
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(
                stored(2L, "quote", "取不到行情", MemoryLesson.STATUS_PENDING),
                stored(1L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE)));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(any(), anyString(), anyString())).thenReturn(1L);

        List<Map<String, Object>> found = service.searchLessons(1L, null, null, 5);

        assertEquals("backtest", found.get(0).get("taskType"));
    }

    @Test
    void filterByTaskType() {
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(
                stored(2L, "quote", "取不到行情", MemoryLesson.STATUS_PENDING),
                stored(1L, "backtest", "数据不足", MemoryLesson.STATUS_PENDING)));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(any(), anyString(), anyString())).thenReturn(1L);

        assertEquals(1, service.searchLessons(1L, null, "backtest", 5).size());
    }

    @Test
    void emptyInputsReturnNothing() {
        assertTrue(service.searchLessons(null, "x", null, 5).isEmpty());
        assertTrue(service.listForUser(null, 10).isEmpty());
        assertTrue(service.history(1L, null, null).isEmpty());
        assertTrue(service.saveLessons(null).isEmpty());
    }

    @Test
    void promptFacingNoteIsNotNeededButListForUserIncludesStatus() {
        when(lessonRepo.findByUserIdOrderByIdDesc(1L))
                .thenReturn(List.of(stored(1L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE)));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptom(eq(1L), anyString(), anyString())).thenReturn(1L);

        assertEquals("active", service.listForUser(1L, 10).get(0).get("status"));
    }
}
