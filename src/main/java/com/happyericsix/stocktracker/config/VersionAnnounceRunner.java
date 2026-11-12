package com.happyericsix.stocktracker.config;

import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.MessageService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.boot.info.BuildProperties;
import org.springframework.context.event.EventListener;
import org.springframework.stereotype.Component;

import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.util.Optional;

/**
 * 服务器更新广播（2026-11-12）：应用启动时若发现是**新构建**（build-info 的构建
 * 时间戳），向全部用户发一条 SYSTEM 消息——"服务器更新了"不再是一件只有部署者
 * 知道的事。
 *
 * <h3>为什么之前没有（用户实跑反馈的根因）</h3>
 * 部署是运维动作（docker compose 换容器），应用代码里没有任何地方知道"我被
 * 重新部署了"；PWA 的版本提示条又受 12 小时比对间隔限制——部署后最长半天
 * 用户毫无感知。本组件补上前半段（后端主动广播），前端比对间隔同步收紧到 30 分钟。
 *
 * <h3>幂等（同一构建不重发）</h3>
 * dedupeKey = "system-version:&lt;构建时间戳&gt;"：saveReport 按键去重，
 * 同一构建的容器重启/回滚再拉起都不会重复广播；每次新构建的时间戳必变，
 * 天然成为"这是一次新部署"的指纹。
 *
 * <h3>fail-open</h3>
 * build-info 缺失（IDE 直跑、测试上下文没有 META-INF/build-info.properties）
 * 或用户表读不了 → 记日志跳过，绝不让广播失败拖垮启动。
 */
@Component
public class VersionAnnounceRunner {

    private static final Logger log = LoggerFactory.getLogger(VersionAnnounceRunner.class);
    private static final DateTimeFormatter CN_TIME =
            DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(ZoneId.of("Asia/Shanghai"));

    private final Optional<BuildProperties> buildProperties;
    private final UserRepository userRepository;
    private final MessageService messageService;

    public VersionAnnounceRunner(Optional<BuildProperties> buildProperties,
                                 UserRepository userRepository,
                                 MessageService messageService) {
        this.buildProperties = buildProperties;
        this.userRepository = userRepository;
        this.messageService = messageService;
    }

    @EventListener(ApplicationReadyEvent.class)
    public void announceOnNewBuild() {
        if (buildProperties.isEmpty() || buildProperties.get().getTime() == null) {
            log.info("无 build-info（IDE/测试运行），跳过版本广播");
            return;
        }
        BuildProperties build = buildProperties.get();
        String builtAt = CN_TIME.format(build.getTime());
        try {
            int sent = 0;
            for (User user : userRepository.findAll()) {
                try {
                    String content = String.format(
                            "【系统更新】v%s · 构建于 %s%n服务器已完成更新并重启。"
                                    + "页面检测到新版本会自动提示（空闲时自动启用，也可点提示条上的「立即刷新」）。",
                            build.getVersion(), builtAt);
                    messageService.saveReport(user, MessageService.TYPE_SYSTEM,
                            "system-version:" + build.getTime().toEpochMilli(),
                            null, content);
                    sent++;
                } catch (Exception e) {
                    log.info("版本广播发送失败 user={}（跳过）：{}", user.getUsername(), e.toString());
                }
            }
            log.info("版本广播完成：v{} 构建于 {}，已通知 {} 个用户",
                    build.getVersion(), builtAt, sent);
        } catch (Exception e) {
            log.info("版本广播失败（不影响启动）：{}", e.toString());
        }
    }
}
