package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.PaperOverviewResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.PaperTradingService;
import lombok.RequiredArgsConstructor;
import org.springframework.security.core.Authentication;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Map;

/**
 * 模拟盘总览：**给"这个功能在哪"一个顶级入口**。
 *
 * <h3>为什么单独一个控制器</h3>
 * 原来模拟盘的读接口全挂在 {@code /api/v1/strategies/{id}/paper/...} 下 ——
 * 也就是说"必须先有某条策略、再点进它的详情页"才看得到任何东西，
 * 用户从首页根本发现不了这个功能（实测反馈：不问 AI 都不知道有模拟盘）。
 * 总览是**跨策略**的读视图，挂在 {@code /api/v1/paper} 下才对得上它的语义。
 *
 * <p>启动/停止仍然复用既有路径（{@code POST /strategies/{id}/paper/start|stop}）：
 * 那是会改账户的动作，不该在这里出现第二个入口。
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/paper")
public class PaperController {

    private final PaperTradingService paperTradingService;
    private final com.happyericsix.stocktracker.service.PortfolioService portfolioService;
    private final com.happyericsix.stocktracker.repository.UserRepository userRepository;

    @GetMapping("/overview")
    public Result<List<PaperOverviewResponse>> overview(Authentication authentication) {
        return Result.success(paperTradingService.getOverview(authentication.getName()));
    }

    /**
     * 组合总览：全部模拟盘叠成一条总曲线的视角（总净值/当日回撤/熔断状态/策略间相关性）。
     * 没有任何已评估账户时 activeStrategies=0，前端整块隐藏。
     */
    @GetMapping("/portfolio")
    public Result<Map<String, Object>> portfolio(Authentication authentication) {
        Long userId = userRepository.findByUsername(authentication.getName())
                .map(u -> u.getId()).orElse(null);
        if (userId == null) {
            return Result.success(Map.of("activeStrategies", 0));
        }
        return Result.success(portfolioService.portfolio(userId));
    }

    // 「决策对账：智囊 vs 规则」（/decision-quality）已于 2026-10-14 移除：
    // 规则与 agent 是同一条流水线的层（规则出信号 → 委员会划授权边界 → 边界内执行），
    // 不是两个可对比的决策者；成交上的 decision_mode 标签会把授权内的规则单记成
    // agent，按来源分组对比测不出干净的东西。单笔方向对账保留在策略详情页的
    // 「决策日志」；委员会的经验回流（strategyExperience/agentOutcomeSince）不受影响。
}
