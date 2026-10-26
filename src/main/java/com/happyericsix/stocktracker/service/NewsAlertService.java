package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.util.HashSet;
import java.util.Set;

/**
 * 新闻预警（2026-10-16，对比同类情报工具后补的主动性缺口）：
 * 解读落库后的**高影响、非中性、置信度达标**事件，若命中某用户的自选股，
 * 写一条 {@code NEWS_ALERT} 消息（幂等）并推手机——重大利好利空主动找人，
 * 而不是等用户自己打开资讯雷达。
 *
 * <h3>纪律</h3>
 * <ol>
 *   <li><b>只在高影响档触发</b>（impact=high）：中低影响也推是情报工具的通病
 *       （把"所有新闻"都塞给用户），这条服务存在的意义恰恰是替用户做第一道筛选；</li>
 *   <li><b>消息幂等</b>：dedupeKey = news-alert:{eventId}:{userId}，重跑解读
 *       （force 再分析）不会重复发；</li>
 *   <li><b>旁路语义</b>：任何失败只记日志，绝不影响解读落库（调用方在事务外调用）；</li>
 *   <li><b>自选命中按 6 位裸代码比较</b>：事件侧 symbol/relatedSymbols 是 SH600519
 *       形态，自选侧可能是带前缀或裸代码，剥前缀后比较才不漏。</li>
 * </ol>
 */
@Service
public class NewsAlertService {

    public static final String TYPE_NEWS_ALERT = "NEWS_ALERT";

    private static final Logger log = LoggerFactory.getLogger(NewsAlertService.class);

    /** 触发档位：只认 high（medium/low 进消息中心就是骚扰）。 */
    private static final String IMPACT_HIGH = "high";
    /** 与 Python 侧研报评级兜底的置信度同档：映射级结论能触发，低置信噪声不能。 */
    private static final double MIN_CONFIDENCE = 0.5;

    private final FavoriteStockRepository favoriteStockRepository;
    private final UserRepository userRepository;
    private final MessageService messageService;
    private final PushService pushService;
    private final ObjectMapper mapper = new ObjectMapper();

    public NewsAlertService(FavoriteStockRepository favoriteStockRepository,
                            UserRepository userRepository,
                            MessageService messageService,
                            PushService pushService) {
        this.favoriteStockRepository = favoriteStockRepository;
        this.userRepository = userRepository;
        this.messageService = messageService;
        this.pushService = pushService;
    }

    /**
     * 事件是否值得预警 + 命中谁。逐用户写消息与推送；全程不抛异常。
     * 在解读事务**提交之后**调用（消息 saveReport 是 REQUIRES_NEW，推送走网络，
     * 都不该被夹在数据事务里）。
     */
    public void notifyWatchers(NewsEvent event) {
        if (!shouldAlert(event)) {
            return;
        }
        Set<String> codes = eventCodes(event);
        if (codes.isEmpty()) {
            return;   // 宏观事件（无个股关联）不进个股预警：它属于"今日信息面"的职责
        }
        for (User user : userRepository.findAll()) {
            try {
                notifyUser(user, event, codes);
            } catch (Exception e) {  // noqa —— 单用户失败不挡其余用户
                log.debug("news alert failed user={} event={}: {}",
                        user.getUsername(), event.getId(), e.getMessage());
            }
        }
    }

    private boolean shouldAlert(NewsEvent event) {
        if (event == null || event.getId() == null) {
            return false;
        }
        if (!IMPACT_HIGH.equals(event.getImpactLevel())) {
            return false;
        }
        String direction = event.getDirection();
        if (direction == null || direction.isBlank() || "中性".equals(direction)) {
            return false;
        }
        return event.getConfidence() != null && event.getConfidence() >= MIN_CONFIDENCE;
    }

    private void notifyUser(User user, NewsEvent event, Set<String> codes) {
        String hitFavorite = null;
        for (FavoriteStock favorite : favoriteStockRepository.findByUserId(user.getId())) {
            if (codes.contains(bareCode(favorite.getStockSymbol()))) {
                hitFavorite = favorite.getStockSymbol();
                break;
            }
        }
        if (hitFavorite == null) {
            return;
        }
        String name = event.getName() != null && !event.getName().isBlank()
                ? event.getName() : hitFavorite;
        String content = String.format("【%s】%s：%s", event.getDirection(), name,
                clip(event.getPlainSummary() != null && !event.getPlainSummary().isBlank()
                        ? event.getPlainSummary() : event.getTitle(), 120));
        // saveReport 自带幂等（dedupeKey）与 SSE 实时推；手机推送是旁路
        messageService.saveReport(user, TYPE_NEWS_ALERT,
                "news-alert:" + event.getId() + ":" + user.getId(),
                hitFavorite, content);
        try {
            pushService.push(user.getUsername(),
                    "新闻预警：" + name + " " + event.getDirection(),
                    clip(event.getTitle(), 80));
        } catch (Exception e) {
            log.debug("news alert push failed: {}", e.getMessage());
        }
    }

    /** 事件涉及的 6 位代码集合：主代码 + relatedSymbols（JSON 数组，SH600519 形态）。 */
    private Set<String> eventCodes(NewsEvent event) {
        Set<String> codes = new HashSet<>();
        String primary = bareCode(event.getSymbol());
        if (!primary.isEmpty()) {
            codes.add(primary);
        }
        String related = event.getRelatedSymbols();
        if (related != null && !related.isBlank() && !"[]".equals(related)) {
            try {
                JsonNode array = mapper.readTree(related);
                if (array.isArray()) {
                    for (JsonNode node : array) {
                        String code = bareCode(node.asText(""));
                        if (!code.isEmpty()) {
                            codes.add(code);
                        }
                    }
                }
            } catch (Exception e) {
                log.debug("related_symbols 解析失败 event={}: {}", event.getId(), e.getMessage());
            }
        }
        return codes;
    }

    /** SH600519 / sz000001 / 600519 → 600519；非 6 位数字返回空串（不猜）。 */
    private static String bareCode(String symbol) {
        String s = symbol == null ? "" : symbol.trim().toUpperCase();
        if (s.startsWith("SH") || s.startsWith("SZ") || s.startsWith("BJ")) {
            s = s.substring(2);
        }
        return s.matches("\\d{6}") ? s : "";
    }

    private static String clip(String text, int max) {
        if (text == null) {
            return "";
        }
        return text.length() <= max ? text : text.substring(0, max) + "…";
    }
}
