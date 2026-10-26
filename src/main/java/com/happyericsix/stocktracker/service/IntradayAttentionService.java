package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;

/**
 * 盘中注意力通道（⑤步，2026-10-16）：agent → 用户的盘中主动消息，**确定性、零 LLM**。
 *
 * <h3>为什么是确定性的</h3>
 * 盘中开一个"每 10 分钟问一次 LLM 值不值得说话"的通道，成本和骚扰都会失控；
 * 而"值得说一句"的信号（登上龙虎榜=关注度异动）本身就是确定性可查的。
 * 轻量通道的纪律：只推送事实 + "要不要看看"，永不含操作建议；幂等键防重发；
 * 高影响新闻预警已在解读落库时即时推（NewsAlertService），这里不重复。
 */
@Service
public class IntradayAttentionService {

    private static final Logger log = LoggerFactory.getLogger(IntradayAttentionService.class);

    private static final ZoneId CN = ZoneId.of("Asia/Shanghai");
    private static final String LHB_SOURCE = "东财·龙虎榜";

    private final StrategyRepository strategyRepository;
    private final NewsEventRepository newsEventRepository;
    private final MessageService messageService;
    private final PushService pushService;

    public IntradayAttentionService(StrategyRepository strategyRepository,
                                    NewsEventRepository newsEventRepository,
                                    MessageService messageService,
                                    PushService pushService) {
        this.strategyRepository = strategyRepository;
        this.newsEventRepository = newsEventRepository;
        this.messageService = messageService;
        this.pushService = pushService;
    }

    /** 一轮检查：全部在跑策略 × 龙虎榜上榜（近 24h）。幂等，可安全重跑。 */
    public int sweep(LocalDateTime now) {
        LocalDateTime since = now.minusHours(24);
        int sent = 0;
        for (Strategy strategy : strategyRepository.findAll()) {
            try {
                if (Boolean.FALSE.equals(strategy.getPaperEnabled())) {
                    continue;
                }
                User user = strategy.getUser();
                if (user == null) {
                    continue;
                }
                sent += notifyLhbIfListed(strategy, user, since);
            } catch (Exception e) {   // noqa —— 单策略失败不挡其余
                log.debug("intraday attention skipped strategy {}: {}",
                        strategy.getId(), e.toString());
            }
        }
        return sent;
    }

    private int notifyLhbIfListed(Strategy strategy, User user, LocalDateTime since) {
        List<NewsEvent> hits = newsEventRepository.findTimeline(
                symbolVariants(strategy.getSymbol()), since, PageRequest.of(0, 10));
        NewsEvent lhb = hits.stream()
                .filter(e -> LHB_SOURCE.equals(e.getSourceName()))
                .findFirst().orElse(null);
        if (lhb == null) {
            return 0;
        }
        String content = "盘中提醒：" + lhb.getTitle()
                + "。上榜只说明资金在异动，方向要看解读；想深入可以让助手查这条的详情。";
        // 幂等键按事件×策略：同一上榜事件对同一策略只提醒一次（跨多轮 sweep）
        Message message = messageService.saveReport(user, "STRATEGY_SENTINEL",
                "lhb-attention:" + lhb.getId() + ":" + strategy.getId(),
                strategy.getSymbol(), content);
        if (message != null) {
            try {
                pushService.push(user.getUsername(), "盘中提醒：" + strategy.getSymbol(),
                        lhb.getTitle());
            } catch (Exception e) {
                log.debug("intraday push failed: {}", e.getMessage());
            }
            return 1;
        }
        return 0;
    }

    /** SH600519 形态 → 双写法（与 NewsService.symbolVariants 同一口径）。 */
    private static List<String> symbolVariants(String symbol) {
        String s = symbol == null ? "" : symbol.trim().toUpperCase();
        if (s.startsWith("SH") || s.startsWith("SZ") || s.startsWith("BJ")) {
            return List.of(s, s.substring(2));
        }
        return List.of("SH" + s, "SZ" + s, s);
    }
}
