package com.happyericsix.stocktracker.service;

import tools.jackson.databind.ObjectMapper;
import com.happyericsix.stocktracker.entity.MemorySkill;
import com.happyericsix.stocktracker.repository.MemorySkillRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * 技能记忆服务（④步，2026-10-16）：CRUD + 取代链版本化 + 三维 scope 过滤注入。
 *
 * <h3>注入语义：软参考</h3>
 * 委员会证据包里的技能标注为"人的经验视角，不是硬规则"——尤其情绪类技能
 * （"全民热议不追高"这种无法数字化的判断）只影响论证角度，不产生触发。
 * 每会最多注入 {@link #MAX_INJECT} 条（token 预算纪律），按版本最新优先。
 */
@Service
public class MemorySkillService {

    private static final Logger log = LoggerFactory.getLogger(MemorySkillService.class);

    /** 单次注入上限：技能是软参考不是证据主体，超过 3 条就是拿 token 买噪声 */
    static final int MAX_INJECT = 3;

    /** scenes 封闭集（表外值解析时丢弃，防脏数据造出无效场景） */
    private static final Set<String> VALID_SCENES = Set.of(
            MemorySkill.SCENE_COMMITTEE, MemorySkill.SCENE_INTRADAY,
            MemorySkill.SCENE_REFLECTION, MemorySkill.SCENE_CHAT);

    private final MemorySkillRepository skillRepo;
    private final ObjectMapper mapper;

    public MemorySkillService(MemorySkillRepository skillRepo, ObjectMapper mapper) {
        this.skillRepo = skillRepo;
        this.mapper = mapper;
    }

    // ==================== 管理（记忆页） ====================

    public List<Map<String, Object>> list(Long userId) {
        return skillRepo.findByUserIdOrderByIdDesc(userId).stream()
                .filter(skill -> skill.getSupersedesId() == null)   // 只看当前版
                .map(this::toMap)
                .toList();
    }

    @Transactional
    public Map<String, Object> create(Long userId, String name, String ruleText,
                                      String origin, List<String> scenes) {
        MemorySkill skill = MemorySkill.builder()
                .userId(userId).name(clip(name, 64)).ruleText(clip(ruleText, 1000))
                .origin(origin == null || origin.isBlank() ? "manual" : clip(origin, 32))
                .status(MemorySkill.STATUS_DRAFT)   // 新技能一律 draft：人显式启用才进决策
                .applicability(writeApplicability(null, scenes, null))
                .version(1)
                .build();
        return toMap(skillRepo.save(skill));
    }

    /** 编辑 = 取代链新行（内容只追加），保留启用状态语义：新版继承旧版 status。 */
    @Transactional
    public Map<String, Object> revise(Long userId, Long skillId, String name, String ruleText) {
        MemorySkill old = owned(userId, skillId);
        MemorySkill next = MemorySkill.builder()
                .userId(userId).name(clip(name, 64)).ruleText(clip(ruleText, 1000))
                .origin(old.getOrigin()).status(old.getStatus())
                .applicability(old.getApplicability())   // 范围不随内容修订漂移，单独接口改
                .version(old.getVersion() + 1)
                .supersedesId(old.getId())
                .build();
        return toMap(skillRepo.save(next));
    }

    /** 启用/停用（就地更新 status——审核元数据，与"内容只追加"不冲突）。 */
    @Transactional
    public Map<String, Object> setStatus(Long userId, Long skillId, String status) {
        if (!MemorySkill.STATUS_ACTIVE.equals(status) && !MemorySkill.STATUS_DISABLED.equals(status)
                && !MemorySkill.STATUS_DRAFT.equals(status)) {
            throw new IllegalArgumentException("非法状态：" + status);
        }
        MemorySkill skill = owned(userId, skillId);
        skill.setStatus(status);
        return toMap(skillRepo.save(skill));
    }

    /** 修改适用范围（subjects/scenes/excludes），就地更新（范围是配置不是记忆内容）。 */
    @Transactional
    public Map<String, Object> setApplicability(Long userId, Long skillId,
                                                List<String> subjects, List<String> scenes,
                                                List<String> excludes) {
        MemorySkill skill = owned(userId, skillId);
        skill.setApplicability(writeApplicability(subjects, scenes, excludes));
        return toMap(skillRepo.save(skill));
    }

    /**
     * 删除草稿（2026-10-18）：仅 draft 可物理删除。
     *
     * <p>边界与"内容只追加"的审计哲学不冲突：append-only 是为了回答"这条决策当时
     * 看到的是哪一版"，而 draft 从未进过决策链路，没有审计价值，留着只是数据残留
     * （用户"建错了删不掉"的数据焦虑正源于此）。进过链路的行（active/disabled）
     * 不提供物理删除——想摆脱就停用，停用即刻不再注入。
     *
     * <p>链条保护：被更新版本 supersedes 的行不删（链条会悬空），从链尾删起。
     */
    @Transactional
    public void delete(Long userId, Long skillId) {
        MemorySkill skill = owned(userId, skillId);
        if (!MemorySkill.STATUS_DRAFT.equals(skill.getStatus())) {
            throw new IllegalArgumentException("只有草稿可以删除；启用过的技能请用停用（保留决策审计链）");
        }
        if (skillRepo.existsBySupersedesId(skillId)) {
            throw new IllegalArgumentException("这条草稿有更新版本（修订链），请先删除最新版本");
        }
        skillRepo.delete(skill);
    }

    // ==================== 注入（委员会/盯盘/反思，内部端点用） ====================

    /**
     * 按 (scene, symbol, strategyId) 过滤可注入技能：active + scene 命中 +
     * subjects 包含（空=全局）+ excludes 不命中（**排除优先**）。软参考语义由
     * 调用方写进证据块注记——这里只负责"哪些技能对口径"。
     */
    public List<Map<String, Object>> injectable(Long userId, String scene,
                                                String symbol, Long strategyId) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (MemorySkill skill : skillRepo.findByUserIdOrderByIdDesc(userId)) {
            if (skill.getSupersedesId() != null
                    || !MemorySkill.STATUS_ACTIVE.equals(skill.getStatus())) {
                continue;
            }
            Scope scope = parse(skill.getApplicability());
            if (scope == null || !scope.scenes.contains(scene)) {
                continue;
            }
            boolean subjectHit = scope.subjects.isEmpty()
                    || (symbol != null && scope.subjects.contains(bare(symbol)))
                    || (strategyId != null && scope.subjects.contains("strategy:" + strategyId));
            String bareSymbol = symbol == null ? "" : bare(symbol);
            String finalStrategy = strategyId == null ? "" : "strategy:" + strategyId;
            boolean excluded = scope.excludes.stream().anyMatch(ex ->
                    ex.equals(bareSymbol) || ex.equals(finalStrategy));
            if (subjectHit && !excluded) {
                out.add(toMap(skill));
                if (out.size() >= MAX_INJECT) {
                    break;
                }
            }
        }
        return out;
    }

    // ==================== 内部工具 ====================

    private MemorySkill owned(Long userId, Long skillId) {
        MemorySkill skill = skillRepo.findById(skillId)
                .orElseThrow(() -> new IllegalArgumentException("技能不存在"));
        if (!skill.getUserId().equals(userId)) {
            throw new IllegalArgumentException("技能不存在");   // 越权与不存在同语：不泄露存在性
        }
        return skill;
    }

    private record Scope(Set<String> subjects, Set<String> scenes, Set<String> excludes) {}

    private Scope parse(String json) {
        if (json == null || json.isBlank()) {
            return new Scope(Set.of(), Set.of(MemorySkill.SCENE_COMMITTEE), Set.of());
        }
        try {
            // readValue(Object.class) 而不是 readTree：tools.jackson 下后者的字符串
            // 重载不可靠（实测抛异常 → 静默走默认 scope，过滤形同虚设——测试抓出来的）
            Map<?, ?> root = mapper.readValue(json, Map.class);
            return new Scope(stringSet(root.get("subjects")), stringSet(root.get("scenes")),
                    stringSet(root.get("excludes")));
        } catch (Exception e) {
            log.debug("applicability 解析失败，按默认（仅委员会）处理: {}", e.getMessage());
            return new Scope(Set.of(), Set.of(MemorySkill.SCENE_COMMITTEE), Set.of());
        }
    }

    private static Set<String> stringSet(Object node) {
        Set<String> out = new java.util.HashSet<>();
        if (node instanceof List<?> list) {
            for (Object item : list) {
                String value = String.valueOf(item == null ? "" : item).trim();
                if (!value.isEmpty()) {
                    out.add(value);
                }
            }
        }
        return out;
    }

    private String writeApplicability(List<String> subjects, List<String> scenes,
                                      List<String> excludes) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("subjects", subjects == null ? List.of() : subjects.stream().map(String::trim).toList());
        // scenes 表外值在写入时就丢弃（封闭集），别把脏数据留给解析端
        out.put("scenes", scenes == null || scenes.isEmpty()
                ? List.of(MemorySkill.SCENE_COMMITTEE)
                : scenes.stream().filter(VALID_SCENES::contains).distinct().toList());
        out.put("excludes", excludes == null ? List.of() : excludes.stream().map(String::trim).toList());
        try {
            return mapper.writeValueAsString(out);
        } catch (Exception e) {
            return "{\"subjects\":[],\"scenes\":[\"committee\"],\"excludes\":[]}";
        }
    }

    /** SH600519/600519 → 600519（与教训 origin 匹配同一归一规则）。 */
    private static String bare(String symbol) {
        String s = symbol.trim().toUpperCase();
        if (s.startsWith("SH") || s.startsWith("SZ") || s.startsWith("BJ")) {
            s = s.substring(2);
        }
        return s;
    }

    private Map<String, Object> toMap(MemorySkill skill) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("id", skill.getId());
        out.put("name", skill.getName());
        out.put("ruleText", skill.getRuleText());
        out.put("origin", skill.getOrigin());
        out.put("status", skill.getStatus());
        out.put("version", skill.getVersion());
        out.put("supersedesId", skill.getSupersedesId());
        out.put("applicability", skill.getApplicability());
        out.put("createdAt", skill.getCreatedAt() == null ? null : skill.getCreatedAt().toString());
        return out;
    }

    private static String clip(String text, int max) {
        String s = text == null ? "" : text.trim();
        if (s.isEmpty()) {
            throw new IllegalArgumentException("内容不能为空");
        }
        return s.length() <= max ? s : s.substring(0, max);
    }
}
