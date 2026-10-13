package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.StrategyHealthEvent;
import com.happyericsix.stocktracker.repository.MemoryFactRepository;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyHealthEventRepository;
import com.happyericsix.stocktracker.service.StrategyService.VerificationSummary;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.util.ArrayList;
import java.util.List;

/**
 * 策略哨兵（健康监控 Phase 1）：**事件驱动**的健康观察。
 *
 * <h3>与日报的分界</h3>
 * {@link PaperReviewReportService} 回答"今天发生了什么"；这里回答"这条策略**正在变坏吗**"。
 * 判据全部是确定性算术（零 LLM 成本、可单测、每次结果一样），挂在每日结算之后跑——
 * 但语义是事件驱动：判据不成立就什么都不落，成立才落一条带证据的 StrategyHealthEvent。
 *
 * <h3>只落事件、不推消息（消息中心收窄后的口径）</h3>
 * 本服务不碰 MessageService；事件落在 strategy_health_events 表，由模拟盘页
 * "哨兵观察"卡展示。STRATEGY_SENTINEL 类型的消息是结算链路另行写的
 * （如结算最终失败告警，见 PaperTradingService），只是复用这里的类型常量。
 *
 * <h3>Phase 1 的三条纪律（与复盘 rubric 同源）</h3>
 * <ol>
 *   <li><b>只观察，不建议</b>：事件里只有证据数字，改不改策略是复盘对话（agent + 回测对比）
 *       的事——建议权在 Phase 3 的提案治理里，且有 matrix 闸门；</li>
 *   <li><b>防骚扰是硬规则</b>：每类事件有独立冷却窗口（按"触发"算，不按"推送"算），
 *       写在数据库查询里，不依赖任何提示词自觉；</li>
 *   <li><b>宁缺毋滥</b>：判据缺基准就不判（没有回测最大回撤就不判回撤越界），
 *       资讯事件只认"已解读 + 高影响 + 高可信 + 方向非中性"。</li>
 * </ol>
 *
 * <h3>失败语义</h3>
 * 单个判据抛异常只跳过该判据（记日志），哨兵整体绝不影响结算——它是结算之后的"附加题"。
 */
@Slf4j
@Service
public class StrategyHealthWatchService {

    private final StrategyHealthEventRepository healthEventRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final PaperEquitySnapshotRepository equitySnapshotRepository;
    private final MemoryFactRepository memoryFactRepository;
    private final NewsEventRepository newsEventRepository;
    /** 可为 null（单测环境）：验证结论读不到就跳过该判据。 */
    private final StrategyService strategyService;

    @Value("${health.idle-days-threshold:10}")
    private int idleDaysThreshold;

    /** 实盘回撤超过"回测最大回撤 × 该倍数"才算越界（1.0 = 刚好等于不算）。 */
    @Value("${health.drawdown-breach-multiplier:1.2}")
    private double drawdownBreachMultiplier;

    @Value("${health.news-credibility-min:70}")
    private int newsCredibilityMin;

    /** 各事件类型的冷却交易日窗口（自然日近似，简单且够用）。 */
    @Value("${health.cooldown-idle-days:10}")
    private int cooldownIdleDays;
    @Value("${health.cooldown-drawdown-days:5}")
    private int cooldownDrawdownDays;
    @Value("${health.cooldown-verification-days:7}")
    private int cooldownVerificationDays;
    @Value("${health.cooldown-news-days:3}")
    private int cooldownNewsDays;
    /** 满仓观察的冷却更长：满仓是**状态**不是**事件**，两周提醒一次足够，天天说就是骚扰。 */
    @Value("${health.cooldown-concentration-days:14}")
    private int cooldownConcentrationDays;

    public StrategyHealthWatchService(StrategyHealthEventRepository healthEventRepository,
                                      PaperTradeRepository paperTradeRepository,
                                      PaperEquitySnapshotRepository equitySnapshotRepository,
                                      MemoryFactRepository memoryFactRepository,
                                      NewsEventRepository newsEventRepository,
                                      StrategyService strategyService) {
        this.healthEventRepository = healthEventRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.equitySnapshotRepository = equitySnapshotRepository;
        this.memoryFactRepository = memoryFactRepository;
        this.newsEventRepository = newsEventRepository;
        this.strategyService = strategyService;
    }

    /**
     * 每日结算后对一条策略做健康检查。返回**本次推送**的事件（冷却内的不返回）。
     */
    public List<StrategyHealthEvent> checkStrategy(Strategy strategy, PaperAccount account, LocalDate today) {
        List<StrategyHealthEvent> fired = new ArrayList<>();
        if (strategy == null || strategy.getId() == null || strategy.getUser() == null || account == null) {
            return fired;
        }
        checkIdleStreak(strategy, account, today).ifPresent(fired::add);
        checkDrawdownBreach(strategy, account, today).ifPresent(fired::add);
        checkVerificationFailed(strategy, today).ifPresent(fired::add);
        checkNewsMaterial(strategy, today).ifPresent(fired::add);
        checkConcentrationFull(strategy, account, today).ifPresent(fired::add);
        return fired;
    }

    // ==================== 判据一：空转（idle_streak） ====================

    /**
     * 上次成交（或开户）以来连续空仓且无成交的交易日数 ≥ 阈值。
     *
     * <p>持仓中不算空转（拿着不动是持仓策略的正常状态）；只有"什么都没发生"才值得提醒
     * ——它区分不了"没信号"还是"规则在当前行情里死了"，这正是要用户去看痕迹/复盘的原因。
     */
    private java.util.Optional<StrategyHealthEvent> checkIdleStreak(Strategy strategy, PaperAccount account,
                                                                    LocalDate today) {
        return guard(StrategyHealthEvent.TYPE_IDLE_STREAK, strategy, today, () -> {
            List<PaperTrade> trades =
                    paperTradeRepository.findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId());
            LocalDate anchor = trades.isEmpty()
                    ? account.getCreatedAt().toLocalDate()
                    : trades.get(0).getTradeDate();

            long idleDays = equitySnapshotRepository
                    .findByStrategyIdOrderByTradeDateAsc(strategy.getId()).stream()
                    .filter(s -> s.getTradeDate().isAfter(anchor))
                    .filter(s -> s.getShares() == null || s.getShares().signum() == 0)
                    .count();
            if (idleDays < idleDaysThreshold) {
                return null;
            }
            String scope = trades.isEmpty()
                    ? "模拟盘启动以来尚未有过一笔成交"
                    : "最近一笔成交（" + anchor + "）之后";
            String evidence = scope + "已连续 " + idleDays + " 个交易日空仓、无任何成交。"
                    + "这可能是行情没给入场机会，也可能是入场条件在当前行情里永远够不到——"
                    + "去结算痕迹看 skip 原因，或让助手复盘一次。";
            return event(strategy, StrategyHealthEvent.TYPE_IDLE_STREAK, evidence, today);
        });
    }

    // ==================== 判据二：回撤越界（drawdown_breach） ====================

    /**
     * 实盘回撤超过"回测最大回撤 × 倍数"。回测基准来自客观事实
     * （{@code backtest_max_drawdown_pct}，subject= strategy:{id}）——没有基准就不判。
     */
    private java.util.Optional<StrategyHealthEvent> checkDrawdownBreach(Strategy strategy, PaperAccount account,
                                                                        LocalDate today) {
        return guard(StrategyHealthEvent.TYPE_DRAWDOWN_BREACH, strategy, today, () -> {
            BigDecimal watermark = account.getHighWatermark();
            if (watermark == null || watermark.signum() <= 0) {
                return null; // 还没有过高水位（未结算过）：不是回撤，是没数据
            }
            BigDecimal drawdownPct = watermark.subtract(account.getEquity())
                    .divide(watermark, 4, RoundingMode.HALF_UP)
                    .multiply(BigDecimal.valueOf(100));
            if (drawdownPct.signum() <= 0) {
                return null; // 在新高附近：健康
            }
            Double backtestMaxDd = readFactDouble(strategy, ObjectiveFactKeys.BACKTEST_MAX_DRAWDOWN_PCT);
            if (backtestMaxDd == null) {
                return null;
            }
            double limit = Math.abs(backtestMaxDd) * drawdownBreachMultiplier;
            if (drawdownPct.doubleValue() <= limit) {
                return null;
            }
            String evidence = "当前回撤 " + drawdownPct.setScale(2, RoundingMode.HALF_UP) + "%，"
                    + "已超过回测最大回撤（" + Math.abs(backtestMaxDd) + "%）的 "
                    + drawdownBreachMultiplier + " 倍上限（" + String.format("%.2f", limit) + "%）。"
                    + "市场正在走这段策略没被检验过的行情。";
            return event(strategy, StrategyHealthEvent.TYPE_DRAWDOWN_BREACH, evidence, today);
        });
    }

    // ==================== 判据三：样本外验证不达标（verification_failed） ====================

    /**
     * 周度 verifyMatrix 的结论（换票换段）平均跑输买入持有。复用
     * {@link StrategyService#latestVerification} 的读视图，不重复实现口径。
     * 没有样本（hasSample=false）不是失败，是"还不能判断"。
     */
    private java.util.Optional<StrategyHealthEvent> checkVerificationFailed(Strategy strategy, LocalDate today) {
        return guard(StrategyHealthEvent.TYPE_VERIFICATION_FAILED, strategy, today, () -> {
            if (strategyService == null) {
                return null;
            }
            VerificationSummary v = strategyService.latestVerification(
                    strategy.getUser().getId(), strategy.getId());
            if (v == null || !v.hasSample() || v.avgExcessPct() == null) {
                return null;
            }
            if (v.avgExcessPct() >= 0) {
                return null;
            }
            String evidence = "样本外验证（换票换段，验证于 " + v.ranAt() + "）："
                    + v.validCells() + " 个样本格里平均跑输买入持有 "
                    + String.format("%.2f", v.avgExcessPct()) + "%，"
                    + "只有 " + v.beatBuyAndHold() + " 格跑赢。"
                    + "这条规则可能只是在这只票、这段行情里凑巧成立。";
            return event(strategy, StrategyHealthEvent.TYPE_VERIFICATION_FAILED, evidence, today);
        });
    }

    // ==================== 判据四：标的重大资讯（news_material） ====================

    /**
     * 标的近 48 小时"已解读 + 高影响 + 高可信 + 方向非中性"的资讯。
     * **只告知，永不据此建议改策略**（信息类事件没有提案权——Phase 3 的治理规则）。
     */
    private java.util.Optional<StrategyHealthEvent> checkNewsMaterial(Strategy strategy, LocalDate today) {
        return guard(StrategyHealthEvent.TYPE_NEWS_MATERIAL, strategy, today, () -> {
            LocalDateTime since = today.atStartOfDay().minusDays(1);
            List<NewsEvent> events = newsEventRepository.findTimeline(
                    symbolCandidates(strategy.getSymbol()), since, PageRequest.of(0, 20));
            List<NewsEvent> material = events.stream()
                    .filter(e -> e.getPlainSummary() != null && !e.getPlainSummary().isBlank())
                    .filter(e -> "high".equals(e.getImpactLevel()))
                    .filter(e -> e.getCredibility() != null && e.getCredibility() >= newsCredibilityMin)
                    .filter(e -> e.getDirection() != null && !"中性".equals(e.getDirection())
                            && !"不判断方向".equals(e.getDirection()))
                    .limit(3)
                    .toList();
            if (material.isEmpty()) {
                return null;
            }
            StringBuilder sb = new StringBuilder("近 48 小时该标的有 ")
                    .append(material.size()).append(" 条高影响、高可信的")
                    .append(material.get(0).getDirection()).append("资讯：");
            for (NewsEvent e : material) {
                sb.append("\n· 《").append(e.getTitle(), 0, Math.min(40, e.getTitle().length()))
                        .append("》（").append(e.getSourceName()).append("，")
                        .append(e.getDirection()).append("，可信 ").append(e.getCredibility()).append("）");
            }
            sb.append("\n这只是信息通报，不构成对策略的任何改动建议。");
            return event(strategy, StrategyHealthEvent.TYPE_NEWS_MATERIAL, sb.toString(), today);
        });
    }

    // ==================== 判据五：满仓单票（concentration_full） ====================

    /**
     * 持仓市值占总净值 ≥ 95%：单一个股的波动 1:1 传导到账户，没有任何现金缓冲。
     *
     * <h3>为什么要有这条（2026-10-12 用户实例）</h3>
     * 策略的 position 缺省是 100%（schema 默认 + 助手提议模板照抄），用户创建策略时
     * 没人指出"这是默认值替你选的满仓"——首笔买入后才发现现金只剩几百元。
     * 哨兵不知道用户的意图（也许就是要满仓），但它能在满仓**成为事实**时说一声：
     * 这条纪律是"只观察，不建议"——报事实和数字，改不改仓位由用户在编辑面板决定。
     *
     * <p>阈值取 95% 而不是 100%：A 股整手规则切下来的零头（差几百块买不起一手）
     * 是满仓的法定产物，卡 100% 会让"严格意义上只有 99.3%"的满仓永远漏判。
     */
    private static final BigDecimal CONCENTRATION_THRESHOLD = new BigDecimal("0.95");

    private java.util.Optional<StrategyHealthEvent> checkConcentrationFull(Strategy strategy,
                                                                           PaperAccount account,
                                                                           LocalDate today) {
        return guard(StrategyHealthEvent.TYPE_CONCENTRATION_FULL, strategy, today, () -> {
            if (account.getShares() == null || account.getShares().signum() <= 0
                    || account.getLastPrice() == null || account.getEquity() == null
                    || account.getEquity().signum() <= 0) {
                return null; // 空仓/无估值：满仓还不成立，不是这条判据的事
            }
            BigDecimal positionValue = account.getShares().multiply(account.getLastPrice());
            BigDecimal ratio = positionValue.divide(account.getEquity(), 4, RoundingMode.HALF_UP);
            if (ratio.compareTo(CONCENTRATION_THRESHOLD) < 0) {
                return null;
            }
            String evidence = "满仓单票：持仓市值 " + Money.amount(positionValue) + " 元，占总净值 "
                    + ratio.multiply(BigDecimal.valueOf(100)).setScale(1, RoundingMode.HALF_UP)
                    + "%，现金仅剩 " + Money.amount(account.getCash()) + " 元 —— 这只股票的波动会"
                    + "1:1 传导到整个账户，没有现金缓冲。这是策略仓位配置（默认 100%）的结果；"
                    + "想留缓冲的话，在「调整策略」里把仓位百分比降下来（比如 50%）。";
            return event(strategy, StrategyHealthEvent.TYPE_CONCENTRATION_FULL, evidence, today);
        });
    }

    // ==================== 冷却、推送与工具 ====================

    /** 统一包装：判据抛异常 → 跳过该判据（哨兵绝不影响结算主链路）。 */
    private java.util.Optional<StrategyHealthEvent> guard(String eventType, Strategy strategy,
                                                          LocalDate today, Detector detector) {
        try {
            StrategyHealthEvent fired = detector.detect();
            if (fired == null) {
                return java.util.Optional.empty();
            }
            if (inCooldown(strategy.getId(), eventType, today)) {
                log.debug("health event {} for strategy {} in cooldown, suppressed (evidence kept in log only)",
                        eventType, strategy.getId());
                return java.util.Optional.empty();
            }
            // 2026-10-10 起哨兵只落事件不推消息（消息中心收窄为预警+系统告警）：
            // 观察通报活在模拟盘页面（PaperReportController 带 cooldown 一起展示）。
            // notified 字段保留"是否已进入用户视野"的语义，由页面读取时置位。
            fired.setNotified(false);
            healthEventRepository.save(fired);
            return java.util.Optional.of(fired);
        } catch (Exception e) {
            log.warn("health check {} failed for strategy {}: {}",
                    eventType, strategy.getId(), e.getMessage());
            return java.util.Optional.empty();
        }
    }

    private boolean inCooldown(Long strategyId, String eventType, LocalDate today) {
        int cooldownDays = switch (eventType) {
            case StrategyHealthEvent.TYPE_IDLE_STREAK -> cooldownIdleDays;
            case StrategyHealthEvent.TYPE_DRAWDOWN_BREACH -> cooldownDrawdownDays;
            case StrategyHealthEvent.TYPE_VERIFICATION_FAILED -> cooldownVerificationDays;
            case StrategyHealthEvent.TYPE_NEWS_MATERIAL -> cooldownNewsDays;
            case StrategyHealthEvent.TYPE_CONCENTRATION_FULL -> cooldownConcentrationDays;
            default -> 7;
        };
        LocalDateTime boundary = today.atStartOfDay().minusDays(cooldownDays);
        return healthEventRepository
                .findTop3ByStrategyIdAndEventTypeOrderByCreatedAtDesc(strategyId, eventType).stream()
                .anyMatch(e -> e.getCreatedAt() != null && e.getCreatedAt().isAfter(boundary));
    }

    /**
     * 哨兵消息的 type。与日报（PAPER_REPORT = "模拟盘复盘"）刻意分开：
     * 哨兵是"系统替你盯着策略"的观察通报，不是复盘 —— 同一个 type 会让
     * 消息中心的图标与标签跟着内容对不上（线上反馈：两条【策略哨兵】
     * 显示成"模拟盘复盘"，看起来"消息通知不对劲"）。
     */
    public static final String TYPE_STRATEGY_SENTINEL = "STRATEGY_SENTINEL";

    private StrategyHealthEvent event(Strategy strategy, String eventType, String evidence, LocalDate today) {
        return StrategyHealthEvent.builder()
                .strategyId(strategy.getId())
                .eventType(eventType)
                .evidence(evidence)
                .notified(false)
                .createdAt(today.atTime(15, 35))
                .build();
    }

    /** 读客观事实的当前值（subject= strategy:{id}），解析失败按缺失处理。 */
    private Double readFactDouble(Strategy strategy, String predicate) {
        try {
            return memoryFactRepository
                    .findActiveByKey(strategy.getUser().getId(),
                            ObjectiveFactKeys.strategySubject(strategy.getId()), predicate)
                    .stream().findFirst()
                    .map(f -> Double.valueOf(f.getFactValue().trim()))
                    .orElse(null);
        } catch (Exception e) {
            return null;
        }
    }

    /**
     * 资讯表里的 symbol 带市场前缀（SH600519），策略里的不带（600519）。
     * 按 README 的市场约定生成候选集，宁多试两个也别漏。
     */
    private static List<String> symbolCandidates(String raw) {
        List<String> out = new ArrayList<>();
        if (raw == null || raw.isBlank()) {
            return out;
        }
        String code = raw.trim().toUpperCase();
        out.add(code);
        String digits = code.replaceAll("[^0-9]", "");
        if (digits.length() == 6 && !code.startsWith("SH") && !code.startsWith("SZ")
                && !code.startsWith("BJ")) {
            String prefix = digits.startsWith("6") ? "SH" : (digits.startsWith("4") || digits.startsWith("8") || digits.startsWith("92") ? "BJ" : "SZ");
            out.add(prefix + digits);
        }
        return out;
    }

    private interface Detector {
        StrategyHealthEvent detect() throws Exception;
    }
}
