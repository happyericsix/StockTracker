package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.UserService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

@RestController
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    // ==================== 个人信息 ====================

    @GetMapping("/api/v1/user/profile")
    public Result<Map<String, Object>> getProfile(Authentication authentication) {
        return Result.success(userService.getProfile(authentication.getName()));
    }

    @PutMapping("/api/v1/user/profile")
    public Result<Map<String, Object>> updateProfile(
            @RequestBody Map<String, Object> updates,
            Authentication authentication) {
        return Result.success(userService.updateProfile(authentication.getName(), updates));
    }

    /**
     * 新手引导完成标记：翻到最后一页点「开始使用」时调用一次。
     * 独立端点而不是塞进 profile PUT——"读完引导"是状态迁移，不是资料编辑，
     * 不该被 profile 表单的半更新语义裹挟。
     */
    @PostMapping("/api/v1/user/onboarding/complete")
    public Result<String> completeOnboarding(Authentication authentication) {
        userService.completeOnboarding(authentication.getName());
        return Result.success("ok");
    }

    @PostMapping("/api/v1/user/change-password")
    public Result<String> changePassword(
            @RequestBody Map<String, String> body,
            Authentication authentication) {
        String oldPassword = body.get("oldPassword");
        String newPassword = body.get("newPassword");
        if (oldPassword == null || newPassword == null) {
            return Result.error(400, "缺少参数");
        }
        userService.changePassword(authentication.getName(), oldPassword, newPassword);
        return Result.success("密码修改成功");
    }

    private final com.happyericsix.stocktracker.service.PushService pushService;

    /**
     * 推送配置连通性测试：按**请求体里**的配置发一条（而不是已保存的配置）——
     * 这样用户能在"保存"之前先验证 key 抄没抄对。
     *
     * <p>sent=true 表示服务商**确认接受**（HTTP 2xx 且业务码成功，2026-10-15 起）；
     * key 抄错会诚实返回 false——之前非 2xx 也回 true，用户等一个永远不响的手机。
     * 每次试推都进推送日志，与正式推送共用同一份"最近推送状态"。
     */
    @PostMapping("/api/v1/user/push/test")
    public Result<Map<String, Object>> testPush(
            @RequestBody Map<String, String> body,
            Authentication authentication) {
        String channel = body.getOrDefault("pushChannel", "");
        String target = body.getOrDefault("pushTarget", "");
        if (channel.isBlank() || target.isBlank()) {
            return Result.error(400, "请先填通道和目标");
        }
        boolean sent = pushService.pushTest(authentication.getName(), channel, target,
                "Stock Tracker 推送测试", "配置成功，成交与预警会推到这里（" + authentication.getName() + "）");
        Map<String, Object> out = new java.util.LinkedHashMap<>();
        out.put("sent", sent);
        return Result.success(out);
    }

    /** 最近推送状态（时间倒序）：回答"推送到底有没有发出去、成没成功"。 */
    @GetMapping("/api/v1/user/push/log")
    public Result<List<Map<String, Object>>> pushLog(
            @RequestParam(defaultValue = "5") int limit,
            Authentication authentication) {
        return Result.success(pushService.recentLogs(authentication.getName(), limit));
    }
}