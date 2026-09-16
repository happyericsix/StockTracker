package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.MemoryFactService;
import com.happyericsix.stocktracker.service.MemoryLessonService;
import com.happyericsix.stocktracker.service.MemoryService;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 记忆管理接口（面向用户，JWT + 归属校验）。
 *
 * <p>这里钉的是<b>越权</b>这条线：所有查询与操作都必须以当前登录用户的 id 为范围。
 * 用户能查/改到别人的记忆，是这个功能里最严重的一类问题。
 */
class MemoryControllerTest {

    private static final Authentication ALICE =
            new UsernamePasswordAuthenticationToken("alice", "n/a", List.of());

    private UserRepository usersOf(Long id) {
        UserRepository repo = Mockito.mock(UserRepository.class);
        when(repo.findByUsername("alice"))
                .thenReturn(Optional.of(User.builder().id(id).username("alice").build()));
        return repo;
    }

    private MemoryController controller(UserRepository users, MemoryFactService facts,
                                        MemoryLessonService lessons, MemoryService memory) {
        return new MemoryController(users, facts, lessons, memory);
    }

    @Test
    void overviewIsScopedToTheLoggedInUser() {
        MemoryFactService facts = Mockito.mock(MemoryFactService.class);
        MemoryLessonService lessons = Mockito.mock(MemoryLessonService.class);
        MemoryService memory = Mockito.mock(MemoryService.class);
        when(memory.overview(7L)).thenReturn(Map.of("episodes", 3));

        Result<Map<String, Object>> result =
                controller(usersOf(7L), facts, lessons, memory).overview(ALICE);

        assertEquals(200, result.getCode());
        assertEquals(3, result.getData().get("episodes"));
        // 关键：用的是登录用户的 id，不是前端传上来的
        verify(memory).overview(7L);
    }

    /**
     * 未登录时不能返回空数据当作"没有记忆" —— 那会让用户以为记忆丢了。
     * 必须明确返回 401，与项目里其它受保护接口一致。
     */
    @Test
    void missingAuthenticationReturns401WithoutTouchingData() {
        MemoryService memory = Mockito.mock(MemoryService.class);
        MemoryController controller = controller(Mockito.mock(UserRepository.class),
                Mockito.mock(MemoryFactService.class), Mockito.mock(MemoryLessonService.class), memory);

        assertEquals(401, controller.overview(null).getCode());
        assertEquals(401, controller.facts(null, null, 50).getCode());
        assertEquals(401, controller.lessons(null, 50).getCode());
        assertEquals(401, controller.persona(null).getCode());
        assertEquals(401, controller.episodes(null).getCode());
        assertEquals(401, controller.retractFact(null, 1L).getCode());

        verify(memory, never()).overview(any());
    }

    @Test
    void unknownUsernameIsTreatedAsUnauthenticated() {
        UserRepository users = Mockito.mock(UserRepository.class);
        when(users.findByUsername("alice")).thenReturn(Optional.empty());
        MemoryService memory = Mockito.mock(MemoryService.class);

        Result<Map<String, Object>> result = controller(users, Mockito.mock(MemoryFactService.class),
                Mockito.mock(MemoryLessonService.class), memory).overview(ALICE);

        assertEquals(401, result.getCode());
        verify(memory, never()).overview(any());
    }

    @Test
    void factsAndLessonsAreQueriedForTheCurrentUserOnly() {
        MemoryFactService facts = Mockito.mock(MemoryFactService.class);
        MemoryLessonService lessons = Mockito.mock(MemoryLessonService.class);
        when(facts.searchFacts(eq(7L), any(), any(), eq("constraint"), eq(50)))
                .thenReturn(List.of(Map.of("predicate", "stop_loss_pct")));
        when(lessons.listForUser(7L, 50)).thenReturn(List.of(Map.of("symptom", "数据不足")));

        MemoryController controller = controller(usersOf(7L), facts, lessons,
                Mockito.mock(MemoryService.class));

        assertEquals("stop_loss_pct", controller.facts(ALICE, "constraint", 50).getData().get(0).get("predicate"));
        assertEquals("数据不足", controller.lessons(ALICE, 50).getData().get(0).get("symptom"));
    }

    @Test
    void retractReportsNotFoundInsteadOfSilentlySucceeding() {
        MemoryFactService facts = Mockito.mock(MemoryFactService.class);
        when(facts.retract(7L, 41L)).thenReturn(false);
        when(facts.retract(7L, 42L)).thenReturn(true);

        MemoryController controller = controller(usersOf(7L), facts,
                Mockito.mock(MemoryLessonService.class), Mockito.mock(MemoryService.class));

        // 撤回不存在的（或别人的）记忆不能被当成成功
        assertEquals(404, controller.retractFact(ALICE, 41L).getCode());
        assertEquals(200, controller.retractFact(ALICE, 42L).getCode());
    }

    @Test
    void lessonReviewRequiresOwnership() {
        MemoryLessonService lessons = Mockito.mock(MemoryLessonService.class);
        when(lessons.activate(7L, 31L)).thenReturn(true);
        when(lessons.retire(7L, 32L)).thenReturn(false);

        MemoryController controller = controller(usersOf(7L), Mockito.mock(MemoryFactService.class),
                lessons, Mockito.mock(MemoryService.class));

        assertEquals(200, controller.activateLesson(ALICE, 31L).getCode());
        assertEquals(404, controller.retireLesson(ALICE, 32L).getCode());
    }

    @Test
    void episodesDoNotLeakInternalIds() {
        MemoryService memory = Mockito.mock(MemoryService.class);
        when(memory.indexCorpus(7L)).thenReturn(Map.of("episodes", List.of(
                Map.of("id", 99L, "sessionKey", "7:2026-09-15", "summary", "用户在建均线策略"))));

        Result<List<Map<String, Object>>> result =
                controller(usersOf(7L), Mockito.mock(MemoryFactService.class),
                        Mockito.mock(MemoryLessonService.class), memory).episodes(ALICE);

        assertEquals(1, result.getData().size());
        assertEquals("7:2026-09-15", result.getData().get(0).get("sessionKey"));
        // 内部主键不外发（前端也用它做 key 才需要；这里刻意去掉）
        assertFalse(result.getData().get(0).containsKey("id"));
    }
}
