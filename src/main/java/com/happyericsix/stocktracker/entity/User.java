package com.happyericsix.stocktracker.entity;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

@Entity
@Table(name = "users")
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class User {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(nullable = false, unique = true)
    private String username;

    /** 密码哈希绝不随实体序列化出网（如 FavoriteStock 误回传实体的场景） */
    @JsonIgnore
    @Column(nullable = false)
    private String password;

    @Column(nullable = false, unique = true)
    private String email;

    /** 手机号仅通过 profile DTO 按需返回，不随实体序列化 */
    @JsonIgnore
    private String phone;

    @Enumerated(EnumType.STRING)
    private Role role;

    /**
     * 消息推送通道：'' / 'bark' / 'serverchan'。
     *
     * <p>推送是**旁路增强**：配置错、网络失败、没配置，都只记日志，绝不影响
     * 站内消息的落库（站内消息永远是主通道，推送只是"手机上能第一时间知道"）。
     */
    private String pushChannel;

    /** 推送目标：bark 填完整服务器地址（如 https://api.day.app/xxxx），serverchan 填 SendKey。 */
    @JsonIgnore
    private String pushTarget;

    /**
     * 我的券商佣金率（百分数，0.025 = 万 2.5）。**模拟盘结算的费用口径**：
     * 佣金是"你和券商的约定"，没有官方统一值，所以放在用户档案里，
     * 而不是写死在代码或策略配置里。null = 用系统默认（万 2.5）。
     * scale=5：万 0.5（0.005%）也要存得下，两位小数会把 0.025 圆成 0.03。
     */
    @jakarta.persistence.Column(precision = 8, scale = 5)
    private java.math.BigDecimal commissionPct;

    /**
     * 是否已读完新手引导（首次登录的全屏使用说明）。**三态**是刻意的：
     * null = 本功能上线前就存在的老账号——不打扰，视为已读（否则升级当晚
     * 所有人被一个关不掉的全屏层拦住，连 e2e 探针账号都会被挡在门外）；
     * false = 新注册账号，首次进入弹引导；
     * true = 已翻到最后一页并点了「开始使用」。
     */
    private Boolean onboardingCompleted;

}