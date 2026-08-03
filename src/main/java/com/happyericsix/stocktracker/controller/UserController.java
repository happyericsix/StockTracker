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

/**
 * 用户管理：QQ 绑定/解绑/状态查询
 *
 * 公开接口（需 JWT）：
 * - POST /api/v1/user/bind-qq/code         生成验证码
 * - GET  /api/v1/user/bind-status          查询绑定状态
 * - POST /api/v1/user/unbind-qq            解绑
 *
 * 内部接口（需 X-Internal-Token）：
 * - POST /api/v1/internal/user/bind-qq     Python webhook 验证 + 绑定
 */
@RestController
@RequiredArgsConstructor
public class UserController {

    private final UserService userService;

    @Value("${internal.api.token:}")
    private String internalToken;

    // ==================== 公开接口（需 JWT） ====================

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

    // ==================== 内部接口（Python webhook 调用） ====================

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
    public ResponseEntity<Result<java.util.Map<String, Object>>> internalLookupByQq(
            @RequestHeader(value = "X-Internal-Token", required = false) String token,
            @RequestParam("qqId") String qqId) {
        if (!isValidInternal(token)) {
            return ResponseEntity.status(401).body(Result.error(401, "Invalid internal token"));
        }
        java.util.Map<String, Object> data = userService.lookupByQqId(qqId);
        if (data == null) {
            return ResponseEntity.status(404).body(Result.error(404, "QQ 未绑定任何用户"));
        }
        return ResponseEntity.ok(Result.success(data));
    }

    @GetMapping("/api/v1/internal/user/favorites")
    public ResponseEntity<Result<java.util.List<java.util.Map<String, Object>>>> internalFavorites(
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
