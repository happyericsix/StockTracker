package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperTradePlan;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.StrategyHealthEvent;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperTradePlanRepository;
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
    /** 前日预案（前日预案制）：报告里的"明日预案"段读最近一份预案。 */
    private final PaperTradePlanRepository planRepository;

    public PaperReportService(StrategyRepository strategyRepository,
                              PaperAccountRepository paperAccountRepository,
                              StrategyHealthEventRepository healthEventRepository,
                              PaperReviewReportService reviewReportService,
                              PaperTradePlanRepository planRepository) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.healthEventRepository = healthEventRepository;
        this.reviewReportService = reviewReportService;
        this.planRepository = planRepository;
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
        result.put("plan", planRowQuietly(strategy));   // 前日预案（最近一份 + 生效状态）
        result.put("sentinels", sentinels);
        return result;
    }

    /** "明日预案"行（前日预案制）：委员会预注册的条件单摘要。没签过预案 = null。 */
    private Map<String, Object> planRowQuietly(Strategy strategy) {
        if (planRepository == null) {
            return null;
        }
        try {
            PaperTradePlan plan = planRepository
                    .findFirstByStrategyIdOrderByIdDesc(strategy.getId()).orElse(null);
            if (plan == null) {
                return null;
            }
            LocalDate today = CnTime.today();
            Map<String, Object> row = new LinkedHashMap<>();
            row.put("planDate", plan.getPlanDate().toString());
            row.put("validUntil", plan.getValidUntilDate().toString());
            row.put("active", !plan.getPlanDate().isAfter(today)
                    && !today.isAfter(plan.getValidUntilDate()));
            row.put("issuedAt", plan.getIssuedAt() == null ? null : plan.getIssuedAt().toString());
            row.put("summary", planSummary(plan));
            return row;
        } catch (Exception e) {
            log.warn("预案段组装失败 strategyId={}: {}", strategy.getId(), e.getMessage());
            return null;
        }
    }

    private String planSummary(PaperTradePlan plan) {
        try {
            var node = new tools.jackson.databind.ObjectMapper()
                    .readTree(plan.getPlanJson());
            String summary = node.path("summary").asText("");
            return summary.isBlank()
                    ? String.format("入场条件 %d 条 / 出场条件 %d 条",
                            node.path("entries").size(), node.path("exits").size())
                    : summary;
        } catch (Exception e) {
            return "";
        }
    }
}
