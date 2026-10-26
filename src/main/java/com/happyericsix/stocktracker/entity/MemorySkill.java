package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;
import java.time.ZoneId;

/**
 * 技能记忆（2026-10-16，④步）：**人审核过的可复用操作准则**。
 *
 * <h3>与教训（MemoryLesson）的分工</h3>
 * 教训 = 系统观察到的事实（症状 → 规律），自动产生、按战绩自动退役；
 * 技能 = 人确认过的判断视角（尤其**不可量化**的东西：情绪、语境、纪律），
 * 来源三种：人工手写 / 内容提炼（视频、新闻）/ 教训升格（≥3 次复发）。
 *
 * <h3>内容只追加（与教训同构）</h3>
 * 编辑 = 追加新版本行指向旧行（supersedesId）；唯一就地更新是 status
 * （审核元数据：draft/active/disabled），与 Message.read 同类。
 *
 * <h3>适用范围 = 三维 scope（JSON，见 SkillService 解析）</h3>
 * subjects：空=全局，或标的/策略 id 列表；scenes：committee/intraday/reflection/chat；
 * excludes：排除项（**排除优先于包含**——"全局但排除 ST 股"的写法靠它）。
 * 新技能默认 draft + 仅 committee 场景：最小授权——AI 产出不直接进决策链路，
 * 人显式启用才放开（这是设计定稿的安全底线）。
 */
@Entity
@Table(name = "memory_skills",
        indexes = {
                @Index(name = "idx_skill_user", columnList = "user_id"),
                @Index(name = "idx_skill_supersedes", columnList = "supersedes_id"),
        })
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class MemorySkill {

    public static final String STATUS_DRAFT = "draft";
    public static final String STATUS_ACTIVE = "active";
    public static final String STATUS_DISABLED = "disabled";

    /** 场景常量（applicability JSON 里 scenes 数组的合法值，封闭集） */
    public static final String SCENE_COMMITTEE = "committee";
    public static final String SCENE_INTRADAY = "intraday";
    public static final String SCENE_REFLECTION = "reflection";
    public static final String SCENE_CHAT = "chat";

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    /** 技能名（人能喊出来的短句，如"情绪冰点不接飞刀"） */
    @Column(nullable = false, length = 64)
    private String name;

    /** 技能正文：可执行的判断准则（"当 X 时倾向于 Y，因为 Z"形态） */
    @Column(nullable = false, length = 1000)
    private String ruleText;

    /** 来源：manual（手写）/ lesson:{id}（升格）/ video / news */
    @Column(length = 32)
    private String origin;

    /** draft（未启用）/ active（启用）/ disabled（停用） */
    @Column(nullable = false, length = 16)
    private String status;

    /** 三维适用范围 JSON：{"subjects":[],"scenes":["committee"],"excludes":[]} */
    @Column(columnDefinition = "TEXT")
    private String applicability;

    @Column(name = "version", nullable = false)
    private Integer version;

    /** 取代链：编辑 = 新行指向旧行，内容永不 UPDATE */
    @Column(name = "supersedes_id")
    private Long supersedesId;

    @Column(name = "created_at", nullable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void onCreate() {
        if (createdAt == null) {
            createdAt = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));
        }
        if (version == null) {
            version = 1;
        }
        if (status == null) {
            status = STATUS_DRAFT;
        }
    }
}
