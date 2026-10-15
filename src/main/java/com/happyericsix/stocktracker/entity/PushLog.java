package com.happyericsix.stocktracker.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.PrePersist;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 推送日志：每一次**真实发出**的手机推送（Bark / Server酱，将来含 Web Push）一行。
 *
 * <h3>为什么需要它（与"宁静失败"的关系）</h3>
 * 旁路纪律仍然是旁路纪律：推送失败绝不影响业务。但"失败只记日志"的代价是
 * 用户配完通道之后再没出过声时，没有任何办法知道"推送其实一直在失败"
 * （key 抄错、服务商换地址、手机卸载了 Bark）。这张表把"推送到底有没有成功"
 * 变成用户可查的事实——Profile 页的"最近推送状态"直接读它。
 *
 * <h3>只记真实发出的尝试</h3>
 * 未配置通道、通道名不认识、目标为空——这些是"没推"，不是"推失败"，
 * 不写行（否则日志里全是自己没配置的噪声）。写入失败本身也只记 WARN
 * （推送日志是旁路的旁路，绝不能反过来拖垮推送）。
 *
 * <p>userId 用裸 Long 而不是 @ManyToOne：与 StrategyHealthEvent 同一模式——
 * 日志行的生死不该被用户实体的生命周期牵连。
 */
@Entity
@Table(name = "push_logs", indexes = {
        @Index(name = "idx_push_logs_user_created", columnList = "user_id, created_at")
})
@Data
@AllArgsConstructor
@NoArgsConstructor
@Builder
public class PushLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "user_id", nullable = false)
    private Long userId;

    /** bark / serverchan（封闭集，与 User.pushChannel 同一取值域） */
    @Column(nullable = false, length = 16)
    private String channel;

    /** 推送标题（截断到列宽，日志要的是"推了什么量级的事"，不是全文） */
    @Column(nullable = false, length = 255)
    private String title;

    /** 推送正文（截断；诊断编码/截断类问题时要能看见实际内容） */
    @Column(length = 500)
    private String body;

    /** 服务商是否接受（HTTP 2xx 且业务码成功）。 */
    @Column(nullable = false)
    private boolean ok;

    /** 失败摘要（HTTP 状态 / 业务错误码 / 异常一句话）；成功为 null。 */
    @Column(length = 500)
    private String error;

    @Column(name = "created_at", nullable = false, updatable = false)
    private LocalDateTime createdAt;

    @PrePersist
    void prePersist() {
        if (createdAt == null) {
            createdAt = com.happyericsix.stocktracker.util.CnTime.now();
        }
    }
}
