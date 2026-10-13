package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.StrategyHealthEvent;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.StrategyHealthEventRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.time.LocalDate;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 模拟盘页面的"今日报告 + 哨兵观察"组装（2026-10-10 消息中心收窄后补的出口）。
 *
 * <h3>为什么是"实时组装"而不是"读消息表"</h3>
 * 日报/简报此前写成消息行的唯一理由是"推送"；推送取消后，落一份冗余副本
 * 反而要面对"结算重跑后页面内容过期"的问题。这里每次取用时现场拼装
 * （PaperReviewReportService 的文本工厂，确定性、不调模型），看到的
 * 永远是当下最新的事实。
 *
 * <h3>报告日期的口径</h3>
 * 不是"今天"，而是**最近一次结算日**（账户 lastEvalAt）：上午打开页面时
 * 今天的结算还没跑（15:30），显示昨天的报告才是真话。
 */
@Service
public class PaperReportService {

    private static final Logger log = LoggerFactory.getLogger(PaperReportService.class);

    /** 哨兵观察在页面上最多展示几条（冷却机制控制着它们的产生频率）。 */
    private static final int SENTINEL_LIMIT = 5;

    private static final Map<String, String> SENTINEL_LABELS = Map.of(
            StrategyHealthEvent.TYPE_IDLE_STREAK, "连续空转",
            StrategyHealthEvent.TYPE_DRAWDOWN_BREACH, "回撤越界",
            StrategyHealthEvent.TYPE_VERIFICATION_FAILED, "样本外验证不达标",
            StrategyHealthEvent.TYPE_NEWS_MATERIAL, "重大资讯");

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final StrategyHealthEventRepository healthEventRepository;
    private final PaperReviewReportService reviewReportService;

    public PaperReportService(StrategyRepository strategyRepository,
                              PaperAccountRepository paperAccountRepository,
                              StrategyHealthEventRepository healthEventRepository,
                              PaperReviewReportService reviewReportService) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.healthEventRepository = healthEventRepository;
        this.reviewReportService = reviewReportService;
    }

    /** 某用户所有模拟盘策略的页面报告（顺序：策略更新时间倒序，与页面卡片一致）。 */
    public List<Map<String, Object>> reportsForUser(Long userId) {
        List<Map<String, Object>> rows = new ArrayList<>();
        if (userId == null) {
            return rows;
        }
        for (Strategy strategy : strategyRepository.findByUserIdAndPaperEnabledTrue(userId)) {
            try {
                rows.add(reportForStrategy(strategy));
            } catch (Exception e) {
                // 单条策略的报告失败不挡其它策略，也不挡页面
                log.warn("组装页面报告失败 strategyId={}: {}", strategy.getId(), e.getMessage());
            }
        }
        return rows;
    }

    private Map<String, Object> reportForStrategy(Strategy strategy) {
        PaperAccount account = paperAccountRepository.findByStrategyId(strategy.getId()).orElse(null);
        // 报告日期 = 最近一次结算日（今天还没结算时显示上一次的，这是事实而不是"今天很平静"）
        LocalDate reportDate = account != null && account.getLastEvalAt() != null
                ? account.getLastEvalAt().toLocalDate()
                : CnTime.today();

        String report = reviewReportService.dailyReportText(strategy, account, reportDate);
        String briefing = null;
        String reportKind = null;
        if (report != null) {
            reportKind = "daily";
        } else {
            briefing = reviewReportService.briefingText(strategy, account, reportDate);
            if (briefing != null) {
                reportKind = "briefing";
            }
        }

        List<Map<String, Object>> sentinels = new ArrayList<>();
        for (StrategyHealthEvent event :
                healthEventRepository.findTop5ByStrategyIdOrderByCreatedAtDesc(strategy.getId())) {
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("at", event.getCreatedAt());
            row.put("type", event.getEventType());
            row.put("label", SENTINEL_LABELS.getOrDefault(event.getEventType(), event.getEventType()));
            row.put("evidence", event.getEvidence());
            sentinels.add(row);
        }
        if (sentinels.size() > SENTINEL_LIMIT) {
            sentinels = sentinels.subList(0, SENTINEL_LIMIT);
        }

        Map<String, Object> result = new LinkedHashMap<>();
        result.put("strategyId", strategy.getId());
        result.put("name", strategy.getName());
        result.put("symbol", strategy.getSymbol());
        result.put("reportDate", reportDate.toString());
        result.put("reportKind", reportKind);       // daily / briefing / null（还没结算过）
        result.put("report", report);               // 有事才说的日报（成交/阻塞跳过）
        result.put("briefing", briefing);           // 安静日子的"为什么没动"
        result.put("sentinels", sentinels);
        return result;
    }
}
