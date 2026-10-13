package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.UserService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

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
     * 这样用户能在"保存"之前先验证 key 抄没抄对。返回是否尝试发送；
     * 发送失败（key 错/网络不通）与未配置的区分由 ok 字段承担。
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
        boolean sent = pushService.pushTo(channel, target,
                "Stock Tracker 推送测试", "配置成功，成交与预警会推到这里（" + authentication.getName() + "）");
        Map<String, Object> out = new java.util.LinkedHashMap<>();
        out.put("sent", sent);
        return Result.success(out);
    }
}