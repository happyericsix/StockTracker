package com.happyericsix.stocktracker.entity;

import com.fasterxml.jackson.annotation.JsonIgnore;
import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.time.LocalDateTime;

/**
 * 同花顺账号绑定。
 *
 * <p>一个用户一行（唯一约束 user_id）。扫码成功后把同花顺给的
 * {@code account + password} 加密存进 {@link #sessionEncrypted}，
 * 之后再同步自选股就不用重新扫码了 —— 这就是「30 天免登录」的实现方式：
 * <b>不是靠定时任务，而是靠"把凭证存下来，下次拿出来用"</b>。
 *
 * <p>⚠️ 凭证是敏感数据：
 * <ul>
 *   <li>{@code sessionEncrypted} 用 AES/GCM 加密，且加了 {@code @JsonIgnore}
 *       —— 绝不随实体序列化出网</li>
 *   <li>加密密钥走配置项 {@code ths.aes.key}（见 application.properties）</li>
 * </ul>
 */
@Entity
@Table(name = "ths_bindings", uniqueConstraints = {
        @UniqueConstraint(columnNames = {"user_id"})
})
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class ThsBinding {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @ManyToOne(fetch = FetchType.LAZY)
    @JoinColumn(name = "user_id")
    private User user;

    /**
     * AES/GCM 加密后的同花顺会话凭证（格式 base64(iv + ciphertext)）。
     *
     * <p>明文形如 JSON：{@code {"account":"mx_xxx","password":"<32位hex>"}}
     */
    @JsonIgnore
    @Column(nullable = false, length = 4096)
    private String sessionEncrypted;

    /**
     * 同花顺侧的凭证过期时间。
     *
     * <p>⚠️ 手机扫码时如果没有勾选「30 天免登录」，同花顺返回 expireTime=0，
     * 这里就是 null。<b>这不代表凭证不能用</b> —— 策略是"先试再说"：
     * 直接拿去调接口，失败了再引导重扫。
     */
    private LocalDateTime expireTime;

    /** 上次成功同步的时间。 */
    private LocalDateTime lastSyncAt;

    /** 上次同步的条数（新建了几只）。 */
    private Integer lastSyncCount;

    /** 最近一次失败原因（可直接给用户看的中文）。 */
    @Column(length = 500)
    private String lastError;
}
