package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.dto.UserLlmConfigRequest;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.UserLlmConfigService;
import lombok.RequiredArgsConstructor;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Map;

/**
 * 用户自带 LLM Key（BYOK，2026-10-25）的配置接口。
 *
 * <pre>
 * GET    /api/v1/user/llm/config   状态（脱敏：只有 maskedKey，明文绝不回传）
 * PUT    /api/v1/user/llm/config   保存（apiKey 留空 = 保留已存 Key）
 * POST   /api/v1/user/llm/test     连通性测试（body 可选：带 = 测表单值，不带 = 复测已存配置）
 * DELETE /api/v1/user/llm/config   清除全部
 * </pre>
 *
 * <p>归属铁律（与 MemoryController 同一条）：所有读写以当前登录用户为范围，
 * 不接受前端传 userId。
 *
 * <p>薄控制器：业务与加密全在 {@link UserLlmConfigService}。
 */
@RestController
@RequestMapping("/api/v1/user/llm")
@RequiredArgsConstructor
public class UserLlmConfigController {

    private static final Logger log = LoggerFactory.getLogger(UserLlmConfigController.class);

    private final UserLlmConfigService userLlmConfigService;
    private final UserRepository userRepository;

    /** 当前登录用户的数字 id（JWT sub 是用户名）。 */
    private Long currentUserId(Authentication authentication) {
        if (authentication == null || authentication.getName() == null) {
            return null;
        }
        return userRepository.findByUsername(authentication.getName())
                .map(u -> u.getId()).orElse(null);
    }

    /** Key 保管状态：enabled（服务器是否配置 LLM_AES_KEY）+ 两个槽位的脱敏摘要。 */
    @GetMapping("/config")
    public Result<Map<String, Object>> status(Authentication authentication) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "未登录");
        }
        return Result.success(userLlmConfigService.status(userId));
    }

    /** 保存配置。校验失败（槽位不配齐/URL 非 https/路径非法）由 GlobalExceptionHandler 统一转中文。 */
    @PutMapping("/config")
    public Result<Map<String, Object>> save(Authentication authentication,
                                            @RequestBody UserLlmConfigRequest request) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "未登录");
        }
        return Result.success("Key 配置已保存", userLlmConfigService.save(userId, request));
    }

    /**
     * 连通性测试。body 可选：带表单 = 保存前先测（不落库）；不带 = 复测已存配置
     * （结论写回 lastTest，个人中心展示"最近一次测试"）。
     */
    @PostMapping("/test")
    public Result<Map<String, Object>> test(Authentication authentication,
                                            @RequestBody(required = false) UserLlmConfigRequest request) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "未登录");
        }
        return Result.success(userLlmConfigService.test(userId, request));
    }

    /** 清除全部配置（主槽 + 备用槽物理删除；此后 AI 功能降级，直到重新配置）。 */
    @DeleteMapping("/config")
    public Result<Void> clear(Authentication authentication) {
        Long userId = currentUserId(authentication);
        if (userId == null) {
            return Result.error(401, "未登录");
        }
        userLlmConfigService.clear(userId);
        return Result.success("已清除全部 Key 配置", null);
    }
}
