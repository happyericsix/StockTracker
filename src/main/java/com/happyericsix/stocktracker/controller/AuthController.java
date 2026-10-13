package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.AuthResponse;
import com.happyericsix.stocktracker.dto.LoginRequest;
import com.happyericsix.stocktracker.dto.RegisterRequest;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.AuthService;

import jakarta.validation.Valid;
import lombok.RequiredArgsConstructor;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/auth")
public class AuthController {
    private final AuthService authService;

    /**
     * 注册开关：默认开（本地开发），公网部署**自己注册完就关掉**
     * （环境变量 {@code AUTH_REGISTRATION_ENABLED=false}，Spring 宽松绑定自动映射）。
     *
     * <p>开着的代价不是"多几个账号"，而是每个注册账号都能无限触发 LLM 调用——
     * 公网上的开放注册 = 陌生人烧你的 DeepSeek 额度（2026-10-05 部署前安全检查的
     * P1 项）。关闭后登录不受影响，只是不再接受新注册。
     */
    @Value("${auth.registration-enabled:true}")
    private boolean registrationEnabled;

    @PostMapping("/register")
    public ResponseEntity<?> register(@Valid @RequestBody RegisterRequest request) {
        if (!registrationEnabled) {
            return ResponseEntity.status(403).body(Result.error(403, "注册已关闭"));
        }
        AuthResponse response = authService.register(request);
        return ResponseEntity.ok(response);
    }

    @PostMapping("/login")
    public ResponseEntity<AuthResponse> login(@Valid @RequestBody LoginRequest request) {
        AuthResponse authResponse = authService.login(request);
        return ResponseEntity.ok(authResponse);
    }
}
