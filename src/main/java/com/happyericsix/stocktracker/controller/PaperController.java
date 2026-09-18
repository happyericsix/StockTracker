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

    @GetMapping("/overview")
    public Result<List<PaperOverviewResponse>> overview(Authentication authentication) {
        return Result.success(paperTradingService.getOverview(authentication.getName()));
    }
}
