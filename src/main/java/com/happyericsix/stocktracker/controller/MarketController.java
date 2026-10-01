package com.happyericsix.stocktracker.controller;

import com.happyericsix.stocktracker.dto.MarketStatusResponse;
import com.happyericsix.stocktracker.dto.Result;
import com.happyericsix.stocktracker.service.MarketSentimentService;
import com.happyericsix.stocktracker.service.MarketStatusService;
import tools.jackson.databind.JsonNode;
import lombok.RequiredArgsConstructor;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

/**
 * 市场状态端点：前端的"休市提示"。
 *
 * <p>用户的原话：*"明明是在休息日，却显示昨收，这里难道不应该提醒用户几天休息吗"*。
 * 休市日腾讯行情返回的仍是上一交易日的收盘价与涨跌幅，而行情接口自身
 * <b>没有任何信息</b>能说明这一点，所以这个端点是必须的 —— 它不是装饰，
 * 它是"这个价是哪一天的"的唯一判据。
 *
 * <p>需要 JWT（落在 {@code anyRequest().authenticated()} 里，与 {@code /stocks/**} 同级）：
 * 它是页面数据的一部分，不是公开元信息。
 *
 * <p>没有缓存注解：缓存策略在 {@link MarketStatusService} 内部（60 秒 TTL），
 * 因为"盘中阶段"这个值必须随分钟变化，走通用的缓存配置反而容易配错 TTL。
 */
@RestController
@RequiredArgsConstructor
@RequestMapping("/api/v1/market")
public class MarketController {

    private final MarketStatusService marketStatusService;
    private final MarketSentimentService marketSentimentService;

    @GetMapping("/status")
    public Result<MarketStatusResponse> status() {
        return Result.success(marketStatusService.current());
    }

    /**
     * 个股散户情绪（千股千评聚合指数 + 对照验证结论）。
     *
     * <p>挂在 /market 命名空间：它是市场数据（对所有人同一份），
     * 不是用户态数据。Python 侧 30 分钟缓存 + Java 侧同步透传；
     * 失败返回 {@code data=null}（前端软失败隐藏整卡），情绪是旁路。
     */
    @GetMapping("/sentiment")
    public Result<JsonNode> sentiment(@RequestParam("symbol") String symbol) {
        return Result.success(marketSentimentService.getSentiment(symbol));
    }
}
