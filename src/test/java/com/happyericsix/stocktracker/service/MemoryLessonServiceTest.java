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
        service = new MemoryLessonService(lessonRepo,
                org.mockito.Mockito.mock(com.happyericsix.stocktracker.repository.StrategyRepository.class),
                new ObjectMapper());
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
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(1L, "generate_strategy", "回测数据不足"))
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
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(1L, "backtest", "回测数据不足"))
                .thenReturn(3L);   // 这次是第 3 次遇到

        List<MemoryLessonService.LessonWriteResult> results =
                service.saveLessons(batch(lesson("backtest", "回测数据不足", "换标的")));

        // 行数就是复发次数：达到阈值会在日志里提示人工审核是否升格 skill，但不自动升格
        assertEquals(3L, results.get(0).occurrences());
        assertEquals("created", results.get(0).action());
    }

    /**
     * symptom 由 LLM 生成，同一现象每次的标点/空白几乎必然不同；不收敛的话
     * "复发 N 次"的精确匹配计数永远数不起来。写入必须以规范化键落库。
     */
    @Test
    void symptomPunctuationAndSpacingAreNormalizedOnWrite() {
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString()))
                .thenReturn(1L);

        service.saveLessons(batch(lesson("backtest", "回测，数据不足。", "换标的")));

        ArgumentCaptor<MemoryLesson> captor = ArgumentCaptor.forClass(MemoryLesson.class);
        verify(lessonRepo).save(captor.capture());
        assertEquals("回测数据不足", captor.getValue().getSymptom());
        // 计数也必须用同一个规范化键查，写入与查询两侧口径一致
        verify(lessonRepo).countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(1L, "backtest", "回测数据不足");
    }

    @Test
    void batchIsCapped() {
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString())).thenReturn(1L);

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
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(1L, "backtest", "数据不足")).thenReturn(2L);
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(1L, "quote", "取不到行情")).thenReturn(1L);

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
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(1L, "backtest", "数据不足")).thenReturn(3L);

        List<Map<String, Object>> found = service.searchLessons(1L, null, null, 5);

        // 提示"可以考虑升格为 skill"，但决定权在人手里
        assertTrue((Boolean) found.get(0).get("promotionSuggested"));
    }

    @Test
    void activeLessonsOutrankPendingOnes() {
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(
                stored(2L, "quote", "取不到行情", MemoryLesson.STATUS_PENDING),
                stored(1L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE)));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString())).thenReturn(1L);

        List<Map<String, Object>> found = service.searchLessons(1L, null, null, 5);

        assertEquals("backtest", found.get(0).get("taskType"));
    }

    @Test
    void filterByTaskType() {
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(
                stored(2L, "quote", "取不到行情", MemoryLesson.STATUS_PENDING),
                stored(1L, "backtest", "数据不足", MemoryLesson.STATUS_PENDING)));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString())).thenReturn(1L);

        assertEquals(1, service.searchLessons(1L, null, "backtest", 5).size());
    }

    // ==================== 记忆白盒：原文 / 句级证据 / 分区（2026-10-15） ====================

    @Test
    void symptomOriginalSentenceIsKeptBesideNormalizedKey() {
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString())).thenReturn(1L);

        service.saveLessons(batch(lesson("backtest", "回测，数据不足。", "换更长周期的标的")));

        ArgumentCaptor<MemoryLesson> captor = ArgumentCaptor.forClass(MemoryLesson.class);
        verify(lessonRepo).save(captor.capture());
        // 键去标点小写（计数/匹配），原文带标点保留（给人读）——两者不再互相冒充
        assertEquals("回测数据不足", captor.getValue().getSymptom());
        assertEquals("回测，数据不足。", captor.getValue().getSymptomText());
    }

    @Test
    void perLessonEvidenceIdsOverrideSessionLevelFallback() {
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString())).thenReturn(1L);

        MemoryLessonRequest withRefs = lesson("backtest", "数据不足", "换标的");
        withRefs.setEvidenceEventIds(List.of(77L, 79L));
        MemoryLessonRequest withoutRefs = lesson("quote", "取不到行情", "换数据源");

        service.saveLessons(batch(withRefs, withoutRefs));

        ArgumentCaptor<MemoryLesson> captor = ArgumentCaptor.forClass(MemoryLesson.class);
        verify(lessonRepo, times(2)).save(captor.capture());
        assertEquals("[77,79]", captor.getAllValues().get(0).getEvidenceEventIds());
        // 没有句级引用的退回整段会话（来源宁可粗，不能断）
        assertEquals("[11,12]", captor.getAllValues().get(1).getEvidenceEventIds());
    }

    @Test
    void weeklyReflectionSessionKeyDerivesStrategyScope() {
        assertEquals("strategy:5", MemoryLessonService.scopeOf("weekly-reflection:strategy:5"));
        assertEquals("chat", MemoryLessonService.scopeOf("1:2026-09-16"));
        assertEquals("chat", MemoryLessonService.scopeOf(null));
        // 畸形键（非数字策略号）不当策略域处理
        assertEquals("chat", MemoryLessonService.scopeOf("weekly-reflection:strategy:abc"));

        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString())).thenReturn(1L);
        MemoryLessonsRequest reflection = batch(lesson("weekly_reflection", "追高被套", "等回踩"));
        reflection.setSessionKey("weekly-reflection:strategy:5");
        service.saveLessons(reflection);
        ArgumentCaptor<MemoryLesson> captor = ArgumentCaptor.forClass(MemoryLesson.class);
        verify(lessonRepo).save(captor.capture());
        assertEquals("strategy:5", captor.getValue().getScope());
    }

    @Test
    void searchFiltersByStrategyScopeSoStrategiesDoNotCrossRead() {
        MemoryLesson s1 = stored(1L, "weekly_reflection", "追高被套", MemoryLesson.STATUS_ACTIVE);
        s1.setScope("strategy:1");
        MemoryLesson s2 = stored(2L, "weekly_reflection", "割在地板上", MemoryLesson.STATUS_ACTIVE);
        s2.setScope("strategy:2");
        MemoryLesson chat = stored(3L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE);
        chat.setScope("chat");
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(s1, s2, chat));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString())).thenReturn(1L);

        // 策略 1 的委员会只看到自己的教训 —— 用户点名要治的"多模拟盘记忆错乱"
        List<Map<String, Object>> found = service.searchLessons(1L, null, null, "strategy:1", 5);
        assertEquals(1, found.size());
        assertEquals(1L, found.get(0).get("id"));
        assertEquals("strategy:1", found.get(0).get("scope"));

        // 不过滤时全量（记忆页展示用）
        assertEquals(3, service.searchLessons(1L, null, null, 5).size());
    }

    @Test
    void outputExposesOriginalSymptomContextAndEvidence() {
        MemoryLesson lesson = stored(1L, "backtest", "回测数据不足", MemoryLesson.STATUS_PENDING);
        lesson.setSymptomText("回测，数据不足。");
        lesson.setScope("strategy:1");
        lesson.setContext("{\"标的\":\"600519\"}");
        lesson.setAttempts("[\"直接重跑\",\"仍然失败\"]");
        lesson.setEvidenceEventIds("[11,13]");
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(lesson));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(1L, "backtest", "回测数据不足")).thenReturn(1L);

        List<Map<String, Object>> found = service.listForUser(1L, 10);

        Map<String, Object> item = found.get(0);
        // 展示用原文，不是规范化键
        assertEquals("回测，数据不足。", item.get("symptom"));
        assertEquals("回测，数据不足。", item.get("symptomRaw"));
        assertEquals("strategy:1", item.get("scope"));
        assertEquals(Map.of("标的", "600519"), item.get("context"));
        assertEquals(List.of("直接重跑", "仍然失败"), item.get("attempts"));
        // Jackson 把小数字解析成 Integer（不是 Long）——断言按内容比
        assertEquals(List.of(11, 13), item.get("evidenceEventIds"));
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
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(eq(1L), anyString(), anyString())).thenReturn(1L);

        assertEquals("active", service.listForUser(1L, 10).get(0).get("status"));
    }

    // ==================== 修订（取代链，2026-10-16 方案一） ====================

    /** 旧行的完整形状：修订要继承它的分区与证据链。 */
    private MemoryLesson revisitable(Long id, String status) {
        MemoryLesson target = stored(id, "weekly_reflection", "追高被套", status);
        target.setSessionKey("weekly-reflection:strategy:5");
        target.setScope("strategy:5");
        target.setEvidenceEventIds("[21,22]");
        target.setProvenance("agent");
        return target;
    }

    @Test
    void userRevisionCreatesSupersedingRowAndInheritsStatus() {
        MemoryLesson target = revisitable(41L, MemoryLesson.STATUS_ACTIVE);
        when(lessonRepo.findByIdForUpdate(41L)).thenReturn(target);
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> {
            MemoryLesson saved = inv.getArgument(0);
            saved.setId(42L);
            return saved;
        });
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(
                1L, "weekly_reflection", "追高被套")).thenReturn(1L);

        MemoryLessonRequest patch = new MemoryLessonRequest();
        patch.setReusableRule("首仓不超过现金 40%，等回踩确认再加仓");
        MemoryLessonService.LessonWriteResult result =
                service.reviseLesson(1L, 41L, patch, "user");

        // 追加新版本指向旧行，旧行内容不动
        ArgumentCaptor<MemoryLesson> captor = ArgumentCaptor.forClass(MemoryLesson.class);
        verify(lessonRepo).save(captor.capture());
        assertEquals(41L, captor.getValue().getSupersedesId());
        assertEquals("revised", result.action());
        // 人改的：继承 active（人就是最高裁决），分区/证据链继承
        assertEquals(MemoryLesson.STATUS_ACTIVE, captor.getValue().getStatus());
        assertEquals("strategy:5", captor.getValue().getScope());
        assertEquals("[21,22]", captor.getValue().getEvidenceEventIds());
        // 没改的字段继承旧行，改了的覆盖
        assertEquals("换更长周期的标的", captor.getValue().getResolution());
        assertEquals("首仓不超过现金 40%，等回踩确认再加仓", captor.getValue().getReusableRule());
        assertEquals("user", captor.getValue().getProvenance());
    }

    @Test
    void agentRevisionIsAlwaysPendingUntilHumanConfirms() {
        when(lessonRepo.findByIdForUpdate(41L)).thenReturn(revisitable(41L, MemoryLesson.STATUS_ACTIVE));
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(
                any(), anyString(), anyString())).thenReturn(1L);

        MemoryLessonService.LessonWriteResult result =
                service.reviseLesson(1L, 41L, new MemoryLessonRequest(), "agent");

        // 防投毒纪律：agent 的修订也一律 pending，过人这一关才生效
        ArgumentCaptor<MemoryLesson> captor = ArgumentCaptor.forClass(MemoryLesson.class);
        verify(lessonRepo).save(captor.capture());
        assertEquals(MemoryLesson.STATUS_PENDING, captor.getValue().getStatus());
        assertEquals("revised", result.action());
    }

    @Test
    void revisionOfSomeoneElsesLessonIsRejected() {
        when(lessonRepo.findByIdForUpdate(9L)).thenReturn(
                MemoryLesson.builder().id(9L).userId(2L).build());

        assertNull(service.reviseLesson(1L, 9L, new MemoryLessonRequest(), "user"));
        verify(lessonRepo, never()).save(any(MemoryLesson.class));
    }

    /**
     * 分叉防护（A-1）：目标已被新版本取代就拒绝再修——同批两条修订同 id、
     * 或锁窗口外的顺序双写，否则会出现两条"当前版本"各自指向旧行。
     */
    @Test
    void revisionOfAnAlreadySupersededTargetIsRejected() {
        when(lessonRepo.findByIdForUpdate(41L)).thenReturn(revisitable(41L, MemoryLesson.STATUS_ACTIVE));
        when(lessonRepo.existsBySupersedesId(41L)).thenReturn(true);

        assertNull(service.reviseLesson(1L, 41L, new MemoryLessonRequest(), "user"));
        verify(lessonRepo, never()).save(any(MemoryLesson.class));
    }

    @Test
    void revisionWithChangedSymptomCarriesTheChainToTheNewKey() {
        MemoryLesson target = revisitable(41L, MemoryLesson.STATUS_ACTIVE);
        when(lessonRepo.findByIdForUpdate(41L)).thenReturn(target);
        when(lessonRepo.save(any(MemoryLesson.class))).thenAnswer(inv -> inv.getArgument(0));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(
                any(), anyString(), anyString())).thenReturn(1L);

        MemoryLessonRequest patch = new MemoryLessonRequest();
        patch.setSymptom("追高被套，隔日低开");
        service.reviseLesson(1L, 41L, patch, "user");

        ArgumentCaptor<MemoryLesson> captor = ArgumentCaptor.forClass(MemoryLesson.class);
        verify(lessonRepo).save(captor.capture());
        // 换键：新行挂新键（规范化），链随行（supersedesId 仍指向旧行）
        assertEquals("追高被套隔日低开", captor.getValue().getSymptom());
        assertEquals("追高被套，隔日低开", captor.getValue().getSymptomText());
        assertEquals(41L, captor.getValue().getSupersedesId());
    }

    @Test
    void searchSkipsSupersededVersionsAndMarksThem() {
        MemoryLesson current = stored(3L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE);
        current.setSupersedesId(2L);
        MemoryLesson stale = stored(2L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE);
        // 检索按 id 倒序：新版本在前，旧版本在后
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(current, stale));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(
                1L, "backtest", "数据不足")).thenReturn(1L);

        List<Map<String, Object>> found = service.searchLessons(1L, null, null, 5);

        // 被取代的旧版本不参与"当前经验"的评选，只剩最新版本
        assertEquals(1, found.size());
        assertEquals(3L, found.get(0).get("id"));
        assertEquals(2L, found.get(0).get("supersedesId"));
        assertFalse((Boolean) found.get(0).get("superseded"));

        // 记忆页全量列表里旧行仍在（白盒），且带上"已修订"标记
        List<Map<String, Object>> all = service.listForUser(1L, 10);
        assertEquals(2, all.size());
        Map<String, Object> staleItem = all.stream()
                .filter(item -> Long.valueOf(2L).equals(item.get("id"))).findFirst().orElseThrow();
        assertTrue((Boolean) staleItem.get("superseded"));
    }

    /**
     * 反问取料（A-3）：对已被取代的历史版本取全文时，必须带上"现在生效的做法"——
     * agent 得知道自己辩护的是旧版本、系统当前相信什么。
     */
    @Test
    void lessonForExposesTheSupersedingRule() {
        MemoryLesson current = stored(3L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE);
        current.setSupersedesId(2L);
        current.setReusableRule("先看数据条数再回测");
        MemoryLesson stale = stored(2L, "backtest", "数据不足", MemoryLesson.STATUS_ACTIVE);
        when(lessonRepo.findById(2L)).thenReturn(Optional.of(stale));
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(current, stale));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(
                any(), anyString(), anyString())).thenReturn(1L);

        Map<String, Object> lesson = service.lessonFor(1L, 2L);

        assertTrue((Boolean) lesson.get("superseded"));
        assertEquals("先看数据条数再回测", lesson.get("supersededByRule"));
        // 别人的教训取不到
        assertNull(service.lessonFor(2L, 2L));
    }


    // ==================== 情境绑定（2026-10-16）：origin 出处 + 同标的加权 ====================

    @org.junit.jupiter.api.Test
    void strategyLessonsCarryOriginWithNameAndSymbol() {
        com.happyericsix.stocktracker.entity.Strategy maotai =
                com.happyericsix.stocktracker.entity.Strategy.builder()
                        .id(3L).name("茅台趋势").symbol("SH600519").build();
        com.happyericsix.stocktracker.repository.StrategyRepository strategyRepo =
                org.mockito.Mockito.mock(com.happyericsix.stocktracker.repository.StrategyRepository.class);
        org.mockito.Mockito.when(strategyRepo.findById(3L))
                .thenReturn(java.util.Optional.of(maotai));
        MemoryLessonService scoped = new MemoryLessonService(
                lessonRepo, strategyRepo, new ObjectMapper());

        MemoryLesson fromStrategy = MemoryLesson.builder()
                .id(9L).userId(1L).taskType("weekly_reflection").symptom("追高")
                .reusableRule("等回踩").status(MemoryLesson.STATUS_ACTIVE)
                .confidence(0.8).scope("strategy:3").build();
        MemoryLesson fromChat = MemoryLesson.builder()
                .id(10L).userId(1L).taskType("chat").symptom("闲聊跑偏")
                .reusableRule("少科普").status(MemoryLesson.STATUS_ACTIVE)
                .confidence(0.8).scope("chat").build();
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(fromChat, fromStrategy));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString()))
                .thenReturn(1L);

        List<Map<String, Object>> found = scoped.searchLessons(1L, null, null, 5);
        Map<String, Object> originOfStrategy = (Map<String, Object>) found.stream()
                .filter(item -> Long.valueOf(9L).equals(item.get("id")))
                .findFirst().orElseThrow().get("origin");
        assertEquals("茅台趋势", originOfStrategy.get("strategyName"));
        assertEquals("SH600519", originOfStrategy.get("symbol"));

        Map<String, Object> originOfChat = (Map<String, Object>) found.stream()
                .filter(item -> Long.valueOf(10L).equals(item.get("id")))
                .findFirst().orElseThrow().get("origin");
        assertEquals("chat", originOfChat.get("scope"));
        assertFalse(originOfChat.containsKey("strategyName"));
    }

    @org.junit.jupiter.api.Test
    @SuppressWarnings("unchecked")
    void sameSymbolLessonRanksAheadWhenBaseScoresTie() {
        // 两条基准分相同的教训：一条来自 600519 策略，一条来自 601988 策略。
        // 询问 600519 时同票教训必须排前——不同股票的教训不能平起平坐地混排。
        com.happyericsix.stocktracker.entity.Strategy sA =
                com.happyericsix.stocktracker.entity.Strategy.builder()
                        .id(1L).name("A").symbol("SH600519").build();
        com.happyericsix.stocktracker.entity.Strategy sB =
                com.happyericsix.stocktracker.entity.Strategy.builder()
                        .id(2L).name("B").symbol("SH601988").build();
        com.happyericsix.stocktracker.repository.StrategyRepository strategyRepo =
                org.mockito.Mockito.mock(com.happyericsix.stocktracker.repository.StrategyRepository.class);
        when(strategyRepo.findById(1L)).thenReturn(java.util.Optional.of(sA));
        when(strategyRepo.findById(2L)).thenReturn(java.util.Optional.of(sB));
        MemoryLessonService scoped = new MemoryLessonService(
                lessonRepo, strategyRepo, new ObjectMapper());

        MemoryLesson other = MemoryLesson.builder()
                .id(21L).userId(1L).taskType("weekly_reflection").symptom("银行追高")
                .reusableRule("等回踩").status(MemoryLesson.STATUS_ACTIVE)
                .confidence(0.8).scope("strategy:2").build();
        MemoryLesson same = MemoryLesson.builder()
                .id(22L).userId(1L).taskType("weekly_reflection").symptom("白酒追高")
                .reusableRule("等回踩").status(MemoryLesson.STATUS_ACTIVE)
                .confidence(0.8).scope("strategy:1").build();
        // 无 query、状态相同、occurrences 相同 → 基准分打平，只由同标的加权分胜负
        when(lessonRepo.findByUserIdOrderByIdDesc(1L)).thenReturn(List.of(other, same));
        when(lessonRepo.countByUserIdAndTaskTypeAndSymptomAndSupersedesIdIsNull(any(), anyString(), anyString()))
                .thenReturn(1L);

        List<Map<String, Object>> found = scoped.searchLessons(
                1L, null, null, null, 5, "SH600519");
        assertEquals(22L, found.get(0).get("id"), "同标的教训应排在最前");

        // 不带 symbol 时顺序回到插入序（打平保持稳定，不引入随机偏差）
        List<Map<String, Object>> neutral = scoped.searchLessons(1L, null, null, 5);
        assertEquals(21L, neutral.get(0).get("id"));
    }
}
