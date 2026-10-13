package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.repository.UserRepository;
import com.happyericsix.stocktracker.service.PaperReportService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 模拟盘页面的报告端点（2026-10-10 消息中心收窄后的新出口）。
 *
 * <p>日报/简报/哨兵观察从"推消息"改为"页面上看"：这里每次实时组装
 * （确定性拼装、不调模型），配合页面上的"调整策略"入口，形成
 * "看到观察 → 调整策略"的闭环，而不是让用户在消息列表里翻旧账。
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/paper")
public class PaperReportController {

    private final PaperReportService paperReportService;
    private final UserRepository userRepository;

    /** 当前用户每个模拟盘策略的最近报告与哨兵观察。 */
    @GetMapping("/report")
    public Result<List<Map<String, Object>>> report(Authentication authentication) {
        Long userId = userRepository.findByUsername(authentication.getName())
                .map(user -> user.getId())
                .orElse(null);
        if (userId == null) {
            return Result.error(401, "登录已失效");
        }
        return Result.success(paperReportService.reportsForUser(userId));
    }
}
