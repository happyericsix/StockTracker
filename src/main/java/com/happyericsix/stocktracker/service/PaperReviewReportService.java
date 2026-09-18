package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.PaperTradeTraceResponse;
import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 盘后复盘报告：把"今天发生了什么"和"这条规则到底行不行"拼成一条**可读的消息**。
 *
 * <h3>为什么必须有它</h3>
 * 痕迹解决的是"查得到"，报告解决的是"用不着去查"。模拟盘最典型的失败不是算错，
 * 而是**用户不再打开它**：账户数字几天不变、没有成交、也没有一句解释，
 * 于是"长期帮我盯着"这件事在两周内自然死亡。所以报告的目标是
 * "每天用 10 秒能看懂今天有没有值得注意的事，以及这条规则此刻的证据是什么"。
 *
 * <h3>三条纪律</h3>
 * <ol>
 *   <li><b>确定性</b>：报告全部由代码拼装（痕迹 + 账户 + 已验证的客观事实），
 *       <b>不调模型</b>。理由与"一句人话"同源：报告是判断依据，
 *       不能每次生成都不一样，也不能因为模型不可用就没有；</li>
 *   <li><b>不假装</b>：没有验证结论就写"还没做样本外验证"，样本量不足就写"还不能判断"，
 *       验证过期就写明最后一次是哪天、多久以前 —— 绝不拿旧结论当此刻的证据；</li>
 *   <li><b>不打扰</b>：只有"今天真的发生了值得知道的事"才推日报（成交或阻塞型跳过），
 *       否则改成一周一条空转期摘要。每天一条"今天什么都没发生"，三周后就是未读堆积。</li>
 * </ol>
 */
@Service
public class PaperReviewReportService {

    private static final Logger log = LoggerFactory.getLogger(PaperReviewReportService.class);

    /** 报告消息的 type。与 ALERT / CHAT_* 并列，前端消息中心按它分流。 */
    public static final String TYPE_PAPER_REPORT = "PAPER_REPORT";

    /** 日报的去重键前缀；同一策略同一天只会有一条。 */
    static final String REPORT_KEY_DAILY = "paper_daily";
    /** 空转期周报的去重键前缀。 */
    static final String REPORT_KEY_WEEKLY = "paper_weekly";

    /** 连续多少个**交易日**没交易才算"值得说的沉默"。低于它就不报（缺失即不报，不假装）。 */
    public static final int IDLE_DAYS_THRESHOLD = 20;
    /** 空转期摘要的覆盖天数。 */
    public static final int IDLE_WINDOW_DAYS = 7;

    private final PaperTraceService paperTraceService;
    private final StrategyService strategyService;
    private final MessageService messageService;

    public PaperReviewReportService(PaperTraceService paperTraceService,
                                    StrategyService strategyService,
                                    MessageService messageService) {
        this.paperTraceService = paperTraceService;
        this.strategyService = strategyService;
        this.messageService = messageService;
    }

    /**
     * 日报：只在这一天"真的发生了事"时推送（成交，或阻塞型跳过）。
     *
     * @return 推送出去的消息；无事可报或已推过则返回 null
     */
    public Message composeDailyReport(Strategy strategy, PaperAccount account, LocalDate tradeDate) {
        try {
            if (strategy == null || strategy.getId() == null || strategy.getUser() == null) {
                return null;
            }
            List<PaperTradeTrace> traces = paperTraceService.listForDay(strategy.getId(), tradeDate);
            if (traces.isEmpty()) {
                // 连痕迹都没有：说明这一天的结算根本没走到留痕那一步（异常路径），
                // 这时候发一条"今天很平静"是**假话**，宁可什么都不发。
                log.debug("No traces for strategy id={} on {}, skipping daily report",
                        strategy.getId(), tradeDate);
                return null;
            }
            List<PaperTradeTrace> noteworthy = traces.stream().filter(PaperReviewReportService::isWorthTelling)
                    .toList();
            if (noteworthy.isEmpty()) {
                log.debug("Nothing noteworthy for strategy id={} on {}", strategy.getId(), tradeDate);
                return null;
            }

            String content = dailyReportText(strategy, account, tradeDate, traces, noteworthy);
            return messageService.saveReport(strategy.getUser(), TYPE_PAPER_REPORT,
                    dailyDedupeKey(strategy.getId(), tradeDate), strategy.getSymbol(), content);
        } catch (Exception e) {
            // 报告是**增强**：结算与痕迹都已经落库，推不出去只是少一条消息
            log.warn("盘后复盘日报生成失败 strategyId={}: {}",
                    strategy == null ? null : strategy.getId(), e.getMessage());
            return null;
        }
    }

    /**
     * 空转期摘要：这一周一条日报都没推过时，改用一条"什么都没发生 + 为什么"的消息。
     *
     * <p>为什么不能简单"没发生就不发"：连续三周零消息，用户会以为系统坏了（或忘了它）。
     * 这条消息的内容不是"今天很平静"，而是**沉默本身的数据**：多少天没动、
     * 净值变化、以及规则层面的结论 —— 也就是"这条规则是不是已经不值得跑了"。
     */
    public Message composeIdleWeeklyReport(Strategy strategy, PaperAccount account, LocalDate weekEnding) {
        try {
            if (strategy == null || strategy.getId() == null || strategy.getUser() == null) {
                return null;
            }
            List<PaperTradeTrace> recent = paperTraceService.listRecent(strategy.getId(), 200);
            LocalDate windowStart = weekEnding.minusDays(IDLE_WINDOW_DAYS);
            boolean reportedThisWeek = recent.stream()
                    .filter(trace -> trace.getTradeDate() != null)
                    .anyMatch(trace -> !trace.getTradeDate().isBefore(windowStart)
                            && isWorthTelling(trace));
            if (reportedThisWeek) {
                return null;   // 这周已经有日报了，不再叠加一条
            }

            long silentDays = silentTradingDays(recent, weekEnding);
            String content = idleReportText(strategy, account, weekEnding, recent, silentDays);
            return messageService.saveReport(strategy.getUser(), TYPE_PAPER_REPORT,
                    REPORT_KEY_WEEKLY + ":" + strategy.getId() + ":" + weekEnding, strategy.getSymbol(), content);
        } catch (Exception e) {
            log.warn("空转期摘要生成失败 strategyId={}: {}",
                    strategy == null ? null : strategy.getId(), e.getMessage());
            return null;
        }
    }

    // ==================== 什么值得告诉用户 ====================

    /**
     * 这一行痕迹值不值得进日报。
     *
     * <p>规则：**成交**值得；**阻塞型跳过**值得（信号来了却被挡住，是用户最需要知道的）；
     * "规则没成立""指标还没算出来"不值得（那是"今天很平静"的另一种写法，
     * 日报里用一句计数带过就够）。
     */
    public static boolean isWorthTelling(PaperTradeTrace trace) {
        if (trace == null) {
            return false;
        }
        if (ExecutionContract.DECISION_SKIP.equals(trace.getDecision())) {
            return PaperTraceService.isBlocking(trace.getSkipReason());
        }
        return true;
    }

    static String dailyDedupeKey(Long strategyId, LocalDate tradeDate) {
        return REPORT_KEY_DAILY + ":" + strategyId + ":" + tradeDate;
    }

    // ==================== 报告正文 ====================

    private String dailyReportText(Strategy strategy, PaperAccount account, LocalDate tradeDate,
                                   List<PaperTradeTrace> all, List<PaperTradeTrace> noteworthy) {
        StringBuilder text = new StringBuilder();
        text.append("【模拟盘复盘】").append(strategy.getName()).append(" · ")
                .append(strategy.getSymbol()).append(" · ").append(tradeDate).append('\n');

        for (PaperTradeTrace trace : noteworthy) {
            text.append("· ").append(PaperTradeTraceResponse.from(trace).getSummary()).append('\n');
        }

        text.append('\n').append(accountLine(account)).append('\n');

        long evaluated = all.stream().filter(t -> ExecutionContract.SETTLEMENT_REALTIME
                .equals(t.getSettlementKind())).count();
        Map<String, Integer> skipCounts = new LinkedHashMap<>();
        for (PaperTradeTrace trace : all) {
            if (ExecutionContract.DECISION_SKIP.equals(trace.getDecision())
                    && trace.getSkipReason() != null) {
                skipCounts.merge(trace.getSkipReason(), 1, Integer::sum);
            }
        }
        text.append("今天的结算：日线 1 次，盘中评估 ").append(evaluated).append(" 次");
        if (!skipCounts.isEmpty()) {
            text.append("；跳过原因：");
            List<String> parts = new ArrayList<>();
            skipCounts.forEach((reason, count) -> parts.add(
                    PaperTradeTraceResponse.sentenceFor(reason) + " ×" + count));
            text.append(String.join("，", parts));
        }
        text.append('\n');

        text.append('\n').append(verificationLine(strategy, tradeDate));
        return text.toString().strip();
    }

    private String idleReportText(Strategy strategy, PaperAccount account, LocalDate weekEnding,
                                  List<PaperTradeTrace> recent, long silentDays) {
        StringBuilder text = new StringBuilder();
        text.append("【模拟盘周报·空转期】").append(strategy.getName()).append(" · ")
                .append(strategy.getSymbol()).append(" · 截至 ").append(weekEnding).append('\n');
        text.append("· 最近 ").append(IDLE_WINDOW_DAYS).append(" 天没有成交，也没有被挡住的信号\n");
        text.append("· 连续无交易日数：").append(silentDays).append(" 个交易日");
        if (silentDays < IDLE_DAYS_THRESHOLD) {
            // 最小样本门槛：不到门槛就说"还不能判断"，不制造"它坏了"的暗示
            text.append("（尚未达到判断「异常沉默」的样本量，需 ≥ ")
                    .append(IDLE_DAYS_THRESHOLD).append(" 个交易日）");
        } else {
            text.append("（已超过 ").append(IDLE_DAYS_THRESHOLD).append(" 个交易日，值得回头看看规则还合不合适）");
        }
        text.append('\n');
        text.append('\n').append(accountLine(account)).append('\n');
        text.append('\n').append(verificationLine(strategy, weekEnding));
        if (!recent.isEmpty()) {
            PaperTradeTrace last = recent.get(recent.size() - 1);
            text.append("\n最近一次留下痕迹的结算：")
                    .append(PaperTradeTraceResponse.from(last).getSummary()).append('\n');
        }
        return text.toString().strip();
    }

    /** 账户一行：净值、相对初始本金的收益、持仓。 */
    private String accountLine(PaperAccount account) {
        if (account == null) {
            return "账户：暂无数据";
        }
        BigDecimal equity = Money.equity(account.getEquity());
        BigDecimal initial = Money.amount(account.getInitialCapital());
        StringBuilder line = new StringBuilder("账户：净值 ").append(equity);
        if (initial != null && initial.signum() != 0 && equity != null) {
            BigDecimal pct = equity.subtract(initial)
                    .multiply(BigDecimal.valueOf(100))
                    .divide(initial, Money.RETURN_SCALE, RoundingMode.HALF_UP);
            // 人看的是 2 位（"+1.20%"），不是 4 位（"+1.2000%"）：口径要精确，
            // 展示要能读 —— 收敛在展示这一层，账里的精度不受影响。
            BigDecimal shown = pct.setScale(2, RoundingMode.HALF_UP);
            line.append("（").append(shown.signum() >= 0 ? "+" : "").append(shown).append("%）");
        }
        line.append("，现金 ").append(Money.amount(account.getCash()))
                .append("，持仓 ").append(Money.price(account.getShares())).append(" 股");
        return line.toString();
    }

    /**
     * 验证结论那一行：报告里**唯一**会提到"这条规则行不行"的地方。
     *
     * <p>三种状态必须说成三句不同的话：没验证过 / 验证过但没有可用样本 / 有结论但已过期。
     * 把它们混成一句"样本外验证：无"，读者就分不清是"没做"还是"做了不算数"。
     */
    private String verificationLine(Strategy strategy, LocalDate asOf) {
        StrategyService.VerificationSummary summary = strategyService == null ? null
                : strategyService.latestVerification(
                        strategy.getUser() == null ? null : strategy.getUser().getId(), strategy.getId());
        if (summary == null) {
            return "规则证据：还没有做过样本外验证（多标的 × 多时段）。\n"
                    + "  在得出结论之前，请把当前表现当作**单个标的的偶然**。";
        }
        StringBuilder line = new StringBuilder("规则证据（样本外验证，");
        line.append(shortDate(summary.ranAt())).append(" 跑）：\n");
        if (!summary.hasSample()) {
            line.append("  这次验证没有可用样本（K 线不足或取数失败），因此它**不构成结论**。\n");
            return line.toString();
        }
        line.append("  · ").append(summary.validCells()).append(" 个有效格子里 ")
                .append(summary.beatBuyAndHold()).append(" 格跑赢买入持有");
        if (summary.avgExcessPct() != null) {
            line.append("；平均超额 ").append(formatPct(summary.avgExcessPct()));
        }
        line.append('\n');
        if (summary.feeDragPct() != null) {
            line.append("  · 摩擦平均吃掉本金 ").append(summary.feeDragPct()).append("%（换手越勤，这部分越大）\n");
        }
        if (summary.engineVersion() != null && !summary.engineVersion().isBlank()) {
            line.append("  · 口径：引擎 ").append(summary.engineVersion())
                    .append("（引擎版本不同则数字不可比）\n");
        }
        long ageDays = ageInDays(summary.ranAt(), asOf);
        if (ageDays > StrategyService.VERIFICATION_STALE_DAYS) {
            line.append("  ⚠️ 这份结论已 ").append(ageDays).append(" 天未更新，"
                    + "不能当作此刻的证据；下次验证会自动覆盖它。\n");
        }
        return line.toString();
    }

    private String formatPct(Double value) {
        // 展示层统一 2 位小数：报告是给人读的，4 位小数只增加噪声（口径的精度在事实里）
        java.math.BigDecimal shown = java.math.BigDecimal.valueOf(value)
                .setScale(2, RoundingMode.HALF_UP);
        return (shown.signum() >= 0 ? "+" : "") + shown + "%";
    }

    /** "2026-09-14T09:00" → "2026-09-14"。格式不认识就原样返回（宁可难看，不要瞎解析）。 */
    private static String shortDate(String iso) {
        if (iso == null) {
            return "时间未知";
        }
        return iso.length() >= 10 ? iso.substring(0, 10) : iso;
    }

    private static long ageInDays(String iso, LocalDate asOf) {
        try {
            LocalDateTime ranAt = LocalDateTime.parse(iso.length() > 19 ? iso.substring(0, 19) : iso);
            return ChronoUnit.DAYS.between(ranAt.toLocalDate(),
                    asOf == null ? LocalDate.now(ZoneId.of("Asia/Shanghai")) : asOf);
        } catch (Exception e) {
            return 0;   // 解析不了就不吓唬用户：宁可不标过期，也不要标一个错的
        }
    }

    /**
     * 连续无交易日数：按痕迹里**最近一次值得说的结算**到今天的自然日折算交易日。
     *
     * <p>刻意用"痕迹"而不是"日历"：真实交易日历还没落地（P-5），
     * 而"上次成交到今天有多少个自然日"是**可查证的**。折算按 5/7 并向下取整，
     * 并在文案里写成"交易日"—— 这个近似必须在文档里说清楚，否则它就是一个假的精确数字。
     */
    private long silentTradingDays(List<PaperTradeTrace> recent, LocalDate asOf) {
        LocalDate lastActive = null;
        for (int i = recent.size() - 1; i >= 0; i--) {
            PaperTradeTrace trace = recent.get(i);
            if (isWorthTelling(trace) && trace.getTradeDate() != null) {
                lastActive = trace.getTradeDate();
                break;
            }
        }
        if (lastActive == null) {
            return 0;
        }
        long calendarDays = ChronoUnit.DAYS.between(lastActive, asOf);
        return Math.max(0, calendarDays * 5 / 7);
    }
}
