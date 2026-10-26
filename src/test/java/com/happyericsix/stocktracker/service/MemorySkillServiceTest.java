package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.MemorySkill;
import com.happyericsix.stocktracker.repository.MemorySkillRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import tools.jackson.databind.ObjectMapper;

import java.util.List;
import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 技能记忆的核心契约（④步，2026-10-16）：
 * 1. 新建一律 draft（AI/用户产出不直接进决策链路——最小授权）；
 * 2. 注入按三维 scope 过滤：scene 命中 + subjects（空=全局）+ excludes **优先于包含**；
 * 3. 注入上限 3 条（软参考不是证据主体，超编就是拿 token 买噪声）。
 */
@ExtendWith(MockitoExtension.class)
class MemorySkillServiceTest {

    @Mock
    private MemorySkillRepository skillRepo;

    /** 手工构造（@InjectMocks 喂不进 mapper——构造器注入下拿到 null，
     *  parse 全走 fallback，过滤测试就白写了。这个坑探针测试抓出来的）。 */
    private MemorySkillService service;

    @org.junit.jupiter.api.BeforeEach
    void setUp() {
        service = new MemorySkillService(skillRepo, new ObjectMapper());
    }

    private MemorySkill skill(Long id, String name, String status, String applicability) {
        return MemorySkill.builder()
                .id(id).userId(7L).name(name).ruleText("准则：" + name)
                .origin("manual").status(status).applicability(applicability)
                .version(1).build();
    }

    @Test
    void createIsAlwaysDraft() {
        when(skillRepo.save(any())).thenAnswer(inv -> inv.getArgument(0));
        Map<String, Object> out = service.create(7L, "情绪冰点不接飞刀",
                "板块冰点且无独立利好时不接下落中的票", null, List.of("committee"));
        assertEquals(MemorySkill.STATUS_DRAFT, out.get("status"));
        assertTrue(String.valueOf(out.get("applicability")).contains("committee"));
    }

    @Test
    @SuppressWarnings("unchecked")
    void injectFiltersBySceneSubjectAndExclude() {
        List<MemorySkill> rows = List.of(
                skill(1L, "全局技能", MemorySkill.STATUS_ACTIVE,
                        "{\"subjects\":[],\"scenes\":[\"committee\"],\"excludes\":[]}"),
                skill(2L, "茅台专属", MemorySkill.STATUS_ACTIVE,
                        "{\"subjects\":[\"600519\"],\"scenes\":[\"committee\"],\"excludes\":[]}"),
                skill(3L, "被排除", MemorySkill.STATUS_ACTIVE,
                        "{\"subjects\":[],\"scenes\":[\"committee\"],\"excludes\":[\"600519\"]}"),
                skill(4L, "聊天场景", MemorySkill.STATUS_ACTIVE,
                        "{\"subjects\":[],\"scenes\":[\"chat\"],\"excludes\":[]}"),
                skill(5L, "草稿不注入", MemorySkill.STATUS_DRAFT,
                        "{\"subjects\":[],\"scenes\":[\"committee\"],\"excludes\":[]}"),
                skill(6L, "已被取代", MemorySkill.STATUS_ACTIVE,
                        "{\"subjects\":[],\"scenes\":[\"committee\"],\"excludes\":[]}"));
        rows.get(5).setSupersedesId(99L);
        when(skillRepo.findByUserIdOrderByIdDesc(7L)).thenReturn(rows);

        List<Map<String, Object>> out = service.injectable(7L, "committee", "SH600519", 12L);
        // 命中：全局 + 茅台专属；排除：被排除（600519）、聊天场景、草稿、被取代
        assertEquals(2, out.size());
        assertEquals("全局技能", out.get(0).get("name"));
        assertEquals("茅台专属", out.get(1).get("name"));
    }

    @Test
    void injectCapsAtThree() {
        List<MemorySkill> rows = new java.util.ArrayList<>();
        for (int i = 1; i <= 5; i++) {
            rows.add(skill((long) i, "技能" + i, MemorySkill.STATUS_ACTIVE,
                    "{\"subjects\":[],\"scenes\":[\"committee\"],\"excludes\":[]}"));
        }
        when(skillRepo.findByUserIdOrderByIdDesc(7L)).thenReturn(rows);
        assertEquals(3, service.injectable(7L, "committee", "", null).size());
    }

    @Test
    void reviseKeepsVersionChain() {
        MemorySkill old = skill(1L, "旧版", MemorySkill.STATUS_ACTIVE, null);
        when(skillRepo.findById(1L)).thenReturn(Optional.of(old));
        when(skillRepo.save(any())).thenAnswer(inv -> inv.getArgument(0));
        Map<String, Object> next = service.revise(7L, 1L, "新版", "新准则");
        assertEquals(2, next.get("version"));
        assertEquals(old.getId(), next.get("supersedesId"));   // 取代链：新版指向旧版
    }

    // ==================== 删除草稿（2026-10-18） ====================

    @Test
    void deleteRemovesDraft() {
        MemorySkill draft = skill(1L, "建错的", MemorySkill.STATUS_DRAFT, null);
        when(skillRepo.findById(1L)).thenReturn(Optional.of(draft));
        when(skillRepo.existsBySupersedesId(1L)).thenReturn(false);
        service.delete(7L, 1L);
        verify(skillRepo).delete(draft);
    }

    @Test
    void deleteRejectsNonDraft() {
        // 启用过的行不提供物理删除：它可能被当时的决策痕迹引用，审计链要留
        MemorySkill active = skill(2L, "在用", MemorySkill.STATUS_ACTIVE, null);
        when(skillRepo.findById(2L)).thenReturn(Optional.of(active));
        assertThrows(IllegalArgumentException.class, () -> service.delete(7L, 2L));
        verify(skillRepo, never()).delete(any());
    }

    @Test
    void deleteRejectsReferencedDraft() {
        // 被更新版本 supersedes 的草稿不删（链条悬空），从链尾删起
        MemorySkill oldDraft = skill(3L, "被修订过的草稿", MemorySkill.STATUS_DRAFT, null);
        when(skillRepo.findById(3L)).thenReturn(Optional.of(oldDraft));
        when(skillRepo.existsBySupersedesId(3L)).thenReturn(true);
        assertThrows(IllegalArgumentException.class, () -> service.delete(7L, 3L));
        verify(skillRepo, never()).delete(any());
    }
}
