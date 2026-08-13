package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.BindQqRequest;
import com.happyericsix.stocktracker.dto.BindQqResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.UserService;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.Map;

@RestController
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    @Value("${internal.api.token:}")
    private String internalToken;

    // ==================== 公开接口（需 JWT）====================

    @PostMapping("/api/v1/user/bind-qq/code")
    public Result<String> generateCode(Authentication authentication) {
        String code = userService.generateBindCode(authentication.getName());
        return Result.success(code);
    }

    @GetMapping("/api/v1/user/bind-status")
    public Result<BindQqResponse> bindStatus(Authentication authentication) {
        BindQqResponse status = userService.getBindStatus(authentication.getName());
        return Result.success(status);
    }

    @PostMapping("/api/v1/user/unbind-qq")
    public Result<BindQqResponse> unbind(Authentication authentication) {
        BindQqResponse result = userService.unbind(authentication.getName());
        return Result.success("解绑成功", result);
    }

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

    // ==================== 内部接口（Python webhook 调用）====================

    @PostMapping("/api/v1/internal/user/bind-qq")
    public ResponseEntity<Result<BindQqResponse>> internalBindQq(
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @RequestBody BindQqRequest request) {
        if (internalToken == null || internalToken.isBlank()) {
            return ResponseEntity.status(500).body(Result.error(500, "Internal token not configured on server"));
        }
        if (!internalToken.equals(token)) {
            return ResponseEntity.status(401).body(Result.error(401, "Invalid internal token"));
        }
        BindQqResponse response = userService.verifyAndBind(request.getQqId(), request.getCode());
        return ResponseEntity.ok(Result.success("绑定成功", response));
    }

    @GetMapping("/api/v1/internal/user/lookup-qq")
    public ResponseEntity<Result<Map<String, Object>>> internalLookupByQq(
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @RequestParam("qqId") String qqId) {
        if (!isValidInternal(token)) {
            return ResponseEntity.status(401).body(Result.error(401, "Invalid internal token"));
        }
        Map<String, Object> data = userService.lookupByQqId(qqId);
        if (data == null) {
            return ResponseEntity.status(404).body(Result.error(404, "QQ 未绑定任何用户"));
        }
        return ResponseEntity.ok(Result.success(data));
    }

    @GetMapping("/api/v1/internal/user/favorites")
    public ResponseEntity<Result<java.util.List<Map<String, Object>>>> internalFavorites(
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @RequestParam("username") String username) {
        if (!isValidInternal(token)) {
            return ResponseEntity.status(401).body(Result.error(401, "Invalid internal token"));
        }
        return ResponseEntity.ok(Result.success(userService.getFavoritesByUsername(username)));
    }

    private boolean isValidInternal(String token) {
        return internalToken != null && !internalToken.isBlank() && internalToken.equals(token);
    }
}
