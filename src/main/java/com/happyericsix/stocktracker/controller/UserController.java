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
}