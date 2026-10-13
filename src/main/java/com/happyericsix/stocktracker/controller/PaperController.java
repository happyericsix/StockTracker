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
    private final com.happyericsix.stocktracker.service.DecisionQualityService decisionQualityService;
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

    /**
     * 决策质量对账：每笔成交 N 个交易日后的方向对错，按决策源（agent/规则）分组对比。
     * 这是"agent 到底比规则好多少"这个问题唯一的答案页。
     */
    @GetMapping("/decision-quality")
    public Result<Map<String, Object>> decisionQuality(
            Authentication authentication,
            @org.springframework.web.bind.annotation.RequestParam(required = false) Integer horizon,
            @org.springframework.web.bind.annotation.RequestParam(required = false) Integer lookback) {
        return Result.success(decisionQualityService.quality(
                authentication.getName(), horizon, lookback));
    }
}
