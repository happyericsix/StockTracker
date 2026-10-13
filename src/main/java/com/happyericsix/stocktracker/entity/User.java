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

}