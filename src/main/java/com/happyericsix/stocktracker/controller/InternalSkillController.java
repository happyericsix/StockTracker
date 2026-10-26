package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.service.MemorySkillService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 技能注入的内部端点（④步，2026-10-16）：Python 委员会/盯盘链路只读取数。
 *
 * <p>照 {@code InternalMemoryController} 的同一条纪律：X-Internal-Token 常量时间
 * 校验、未配置一律拒绝；只有 GET（agent 不能改技能——启用/停用是人的动作）。
 */
@RestController
@RequestMapping("/api/v1/internal/skills")
public class InternalSkillController {

    private static final Logger log = LoggerFactory.getLogger(InternalSkillController.class);

    private final MemorySkillService skillService;
    private final String internalToken;

    public InternalSkillController(MemorySkillService skillService,
                                   @Value("${internal.api-token:}") String internalToken) {
        this.skillService = skillService;
        this.internalToken = internalToken;
    }

    /**
     * 按场景过滤的可注入技能（软参考）。
     *
     * @param scene      committee / intraday / reflection / chat（封闭集，表外值返回空）
     * @param symbol     决策标的（可空；subjects 按裸代码匹配）
     * @param strategyId 策略 id（可空；subjects 按 "strategy:N" 匹配）
     */
    @GetMapping("/inject")
    public ResponseEntity<Map<String, Object>> inject(
            @RequestParam Long userId,
            @RequestParam String scene,
            @RequestParam(required = false) String symbol,
            @RequestParam(required = false) Long strategyId,
            @RequestHeader(value = "X-Internal-Token", required = false) String token) {
        if (!authorized(token)) {
            return unauthorized();
        }
        List<Map<String, Object>> skills = skillService.injectable(userId, scene, symbol, strategyId);
        Map<String, Object> body = new LinkedHashMap<>();
        body.put("skills", skills);
        body.put("note", "软参考：人的经验视角（含情绪/语境类不可量化判断），影响论证角度，不是硬规则、不构成触发");
        return ResponseEntity.ok(body);
    }

    private boolean authorized(String token) {
        if (internalToken == null || internalToken.isBlank()) {
            log.error("internal.api-token 未配置，拒绝技能注入接口访问");
            return false;
        }
        if (token == null) {
            return false;
        }
        return java.security.MessageDigest.isEqual(
                token.getBytes(java.nio.charset.StandardCharsets.UTF_8),
                internalToken.getBytes(java.nio.charset.StandardCharsets.UTF_8));
    }

    private ResponseEntity<Map<String, Object>> unauthorized() {
        return ResponseEntity.status(HttpStatus.UNAUTHORIZED).body(Map.of("error", "unauthorized"));
    }
}
