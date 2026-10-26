package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.MemorySkillService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.*;

import java.util.List;
import java.util.Map;

/**
 * 技能记忆端点（记忆页「技能」卡，④步 2026-10-16）。
 *
 * <p>安全默认在服务层：新建一律 draft（不进决策链路），启用/停用/改范围都是
 * 用户显式动作。DTO 用 Map 保持轻（技能字段少且稳定）。
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/skills")
public class SkillController {

    private final com.happyericsix.stocktracker.repository.UserRepository userRepository;
    private final MemorySkillService skillService;

    private Long userId(Authentication auth) {
        return userRepository.findByUsername(auth.getName())
                .orElseThrow(() -> new IllegalArgumentException("用户不存在")).getId();
    }

    @GetMapping
    public Result<List<Map<String, Object>>> list(Authentication auth) {
        return Result.success(skillService.list(userId(auth)));
    }

    public record CreateRequest(String name, String ruleText, String origin, List<String> scenes) {}

    @PostMapping
    public Result<Map<String, Object>> create(@RequestBody CreateRequest body,
                                              Authentication auth) {
        return Result.success(skillService.create(
                userId(auth), body.name(), body.ruleText(), body.origin(), body.scenes()));
    }

    public record ReviseRequest(String name, String ruleText) {}

    @PutMapping("/{id}")
    public Result<Map<String, Object>> revise(@PathVariable Long id,
                                              @RequestBody ReviseRequest body,
                                              Authentication auth) {
        return Result.success(skillService.revise(userId(auth), id, body.name(), body.ruleText()));
    }

    public record StatusRequest(String status) {}

    @PostMapping("/{id}/status")
    public Result<Map<String, Object>> setStatus(@PathVariable Long id,
                                                 @RequestBody StatusRequest body,
                                                 Authentication auth) {
        return Result.success(skillService.setStatus(userId(auth), id, body.status()));
    }

    public record ScopeRequest(List<String> subjects, List<String> scenes, List<String> excludes) {}

    @PostMapping("/{id}/scope")
    public Result<Map<String, Object>> setScope(@PathVariable Long id,
                                                @RequestBody ScopeRequest body,
                                                Authentication auth) {
        return Result.success(skillService.setApplicability(
                userId(auth), id, body.subjects(), body.scenes(), body.excludes()));
    }

    /** 删除草稿（仅 draft；启用过的走停用，保留审计链）。越权与不存在同语。 */
    @DeleteMapping("/{id}")
    public Result<String> delete(@PathVariable Long id, Authentication auth) {
        skillService.delete(userId(auth), id);
        return Result.success("已删除");
    }
}
