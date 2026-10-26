package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperEquitySnapshotRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;
import tools.jackson.databind.node.ArrayNode;
import tools.jackson.databind.node.ObjectNode;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.YearMonth;
import java.util.ArrayList;
import java.util.List;
import java.util.Map;

/**
 * 月度小结 + 季度策略信（2026-10-15）：把"上个自然月/上个季度这条策略干得怎么样"
 * 拼成一封消息，进消息中心 + 手机推送。
 *
 * <h3>两档的分工：月度看数字，季度看逻辑</h3>
 * 月度小结是纯统计（收益/回撤/胜率/空仓），确定性代码拼装，与日报同一条"不调模型"纪律；
 * 季度信（上月是季末月时**取代**月度小结，不双发）在数字底座之上多一段 LLM 评语 ——
 * 回答"策略的逻辑这个季度还成立吗、参数有没有值得调整的提议"。评语是**提议不是动作**：
 * 永不自动改配置（那是"参数调优闭环"的事，本系统刻意没有）。
 *
 * <h3>与周反思的分工</h3>
 * 周反思面向委员会（教训回流提示词，宁缺毋滥）；季度信面向用户（一封可读的信）。
 * 一个改机器的行为，一个告诉人发生了什么。
 *
 * <h3>幂等即调度</h3>
 * 任务每周五 16:40 都会问一遍"上个月发过没有"（幂等键 {@code periodic:monthly:{sid}:2026-09}）：
 * 发过就静默返回。这样国庆那种"月首周五是假日"的场景不需要任何补跑逻辑 ——
 * 假日跳过后，下一个周五自然补上。
 */
@Service
public class PaperPeriodicReviewService {

    private static final Logger log = LoggerFactory.getLogger(PaperPeriodicReviewService.class);

    /** 消息中心类型（已在 MessageService.CENTER_TYPES 的口径内）。 */
    public static final String MESSAGE_TYPE = "STRATEGY_REVIEW";

    private final StrategyRepository strategyRepository;
    private final PaperAccountRepository paperAccountRepository;
    private final PaperTradeRepository paperTradeRepository;
    private final PaperEquitySnapshotRepository paperEquitySnapshotRepository;
    private final MemoryLessonService memoryLessonService;
    private final PaperReviewReportService paperReviewReportService;
    private final StrategyClient strategyClient;
    private final MessageService messageService;
    private final PushService pushService;
    private final ObjectMapper mapper = new ObjectMapper();

    public PaperPeriodicReviewService(StrategyRepository strategyRepository,
                                      PaperAccountRepository paperAccountRepository,
                                      PaperTradeRepository paperTradeRepository,
                                      PaperEquitySnapshotRepository paperEquitySnapshotRepository,
                                      MemoryLessonService memoryLessonService,
                                      PaperReviewReportService paperReviewReportService,
                                      StrategyClient strategyClient,
                                      MessageService messageService,
                                      PushService pushService) {
        this.strategyRepository = strategyRepository;
        this.paperAccountRepository = paperAccountRepository;
        this.paperTradeRepository = paperTradeRepository;
        this.paperEquitySnapshotRepository = paperEquitySnapshotRepository;
        this.memoryLessonService = memoryLessonService;
        this.paperReviewReportService = paperReviewReportService;
        this.strategyClient = strategyClient;
        this.messageService = messageService;
        this.pushService = pushService;
    }

    /** 一个复盘区间的元数据：起止日 + 幂等标签。 */
    record Period(LocalDate start, LocalDate end, String label, String quarterLabel) {
        static Period ofMonth(YearMonth month) {
            return new Period(month.atDay(1), month.atEndOfMonth(), month.toString(), null);
        }

        static Period ofQuarter(YearMonth quarterEndMonth) {
            Period p = ofMonth(quarterEndMonth);
            int q = (quarterEndMonth.getMonthValue() - 1) / 3 + 1;
            return new Period(p.start(), p.end(), p.label(),
                    quarterEndMonth.getYear() + "-Q" + q);
        }

        boolean isQuarterEnd() {
            return quarterLabel != null;
        }
    }

    /**
     * 定时入口：复盘"上一个自然月"。每条 paper 策略独立 try/catch ——
     * 一条的失败不能挡住别人家的信。
     */
    public void runPeriodicReports(LocalDate today) {
        YearMonth prevMonth = YearMonth.from(today).minusMonths(1);
        boolean quarterEnd = List.of(3, 6, 9, 12).contains(prevMonth.getMonthValue());
        Period period = quarterEnd ? Period.ofQuarter(prevMonth) : Period.ofMonth(prevMonth);
        for (Strategy strategy : strategyRepository.findByPaperEnabledTrue()) {
            try {
                reportOneQuietly(strategy, period);
            } catch (Exception e) {
                log.error("周期复盘失败 strategy id={} period={}: {}",
                        strategy.getId(), period.label(), e.getMessage(), e);
            }
        }
    }

    private void reportOneQuietly(Strategy strategy, Period period) {
        if (strategy == null || strategy.getId() == null || strategy.getUser() == null) {
            return;
        }
        String dedupeKey = (period.isQuarterEnd() ? "periodic:quarterly:" : "periodic:monthly:")
                + strategy.getId() + ":" + (period.isQuarterEnd() ? period.quarterLabel() : period.label());
        if (messageService.reportSent(dedupeKey)) {
            return;   // 本期已发（或假期顺延后的第二个周五来问）—— 幂等即调度
        }
        // 出生判定：策略首个快照晚于区间结束 → 那时它还不存在，发信是噪声（跳过）。
        // 反过来"跑过、但这段没快照"（异常沉默）仍然要发 —— 那封"本期没有结算快照"的信
        // 正是用户需要知道的事。两种沉默在这里分家。
        LocalDate firstEver = paperEquitySnapshotRepository
                .findByStrategyIdOrderByTradeDateAsc(strategy.getId()).stream()
                .map(PaperEquitySnapshot::getTradeDate)
                .filter(d -> d != null)
                .findFirst()
                .orElse(null);
        if (firstEver != null && firstEver.isAfter(period.end())) {
            log.info("Skip periodic report strategy id={} period={}：首个快照 {} 晚于区间结束（当时还没在跑）",
                    strategy.getId(), period.label(), firstEver);
            return;
        }
        PaperAccount account = paperAccountRepository.findByStrategyId(strategy.getId()).orElse(null);
        String title = period.isQuarterEnd() ? "季度策略信" : "月度小结";
        String text = period.isQuarterEnd()
                ? quarterlyLetterText(strategy, account, period)
                : monthlySummaryText(strategy, account, period);

        messageService.saveReport(strategy.getUser(), MESSAGE_TYPE, dedupeKey,
                strategy.getSymbol(), text);
        pushService.push(strategy.getUser().getUsername(),
                title + " · " + strategy.getName(), snippet(text));
        log.info("{} sent strategy id={} period={}", title, strategy.getId(), period.label());
    }

    // ==================== 月度小结（纯统计，不调模型） ====================

    public String monthlySummaryText(Strategy strategy, PaperAccount account, Period period) {
        StringBuilder text = new StringBuilder();
        text.append("【模拟盘月度小结】").append(strategy.getName()).append(" · ")
                .append(strategy.getSymbol()).append(" · ").append(period.label()).append('\n');
        appendStatsBlock(text, strategy, account, period);
        text.append('\n').append(paperReviewReportService.verificationLine(strategy, period.end())).append('\n');
        return text.toString().strip();
    }

    // ==================== 季度策略信（数字底座 + 评语半环） ====================

    public String quarterlyLetterText(Strategy strategy, PaperAccount account, Period period) {
        StringBuilder text = new StringBuilder();
        text.append("【模拟盘季度策略信】").append(strategy.getName()).append(" · ")
                .append(strategy.getSymbol()).append(" · ").append(period.quarterLabel())
                .append("（").append(period.start()).append(" ~ ").append(period.end()).append("）\n");
        PeriodStats stats = appendStatsBlock(text, strategy, account, period);
        text.append('\n').append(lessonsLine(strategy)).append('\n');
        text.append('\n').append(paperReviewReportService.verificationLine(strategy, period.end())).append('\n');
        text.append('\n').append(commentaryBlock(strategy, account, period, stats));
        return text.toString().strip();
    }

    /** 评语半环：1 次 LLM 调用；失败/不可用 → 明写"未生成评语"，数字底座照发（不假装）。 */
    private String commentaryBlock(Strategy strategy, PaperAccount account, Period period, PeriodStats stats) {
        JsonNode letter = null;
        try {
            // BYOK：评语花的是这个用户的 Key（未配置 → "未生成评语"，数字底座照发）
            letter = strategyClient.quarterlyReview(
                    letterPayload(strategy, period, stats),
                    strategy.getUser() != null ? strategy.getUser().getId() : null);
        } catch (Exception e) {
            log.info("季度评语调用失败 strategy id={}（信仍发出，只带数字）：{}", strategy.getId(), e.toString());
        }
        String verdict = letter == null ? "" : letter.path("letter").path("verdict").asText("");
        String suggestions = letter == null ? "" : letter.path("letter").path("suggestions").asText("");
        if (verdict.isBlank()) {
            return "策略评语：本季未生成（模型不可用或返回不可解析）。以上为确定性数字底座，"
                    + "评语下周复盘时会随周验证更新，不阻塞本信。";
        }
        StringBuilder out = new StringBuilder("策略评语（AI 生成，仅供参考，不构成投资建议）：\n")
                .append(verdict);
        if (!suggestions.isBlank()) {
            out.append("\n参数提议（需你确认，系统不会自动修改任何配置）：").append(suggestions);
        }
        return out.toString();
    }

    private JsonNode letterPayload(Strategy strategy, Period period, PeriodStats stats) {
        ObjectNode payload = mapper.createObjectNode();
        ObjectNode meta = payload.putObject("strategy");
        meta.put("name", strategy.getName());
        meta.put("symbol", strategy.getSymbol());
        meta.put("decisionMode", ExecutionContract.normalizeDecisionMode(strategy.getDecisionMode()));
        ObjectNode window = payload.putObject("period");
        window.put("label", period.quarterLabel());
        window.put("start", period.start().toString());
        window.put("end", period.end().toString());
        payload.set("stats", stats == null ? mapper.createObjectNode() : stats.toJson(mapper));
        ArrayNode trades = payload.putArray("trades");
        int added = 0;
        for (PaperTrade trade : paperTradeRepository
                .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId())) {
            if (trade.getTradeDate() == null || trade.getTradeDate().isBefore(period.start())
                    || trade.getTradeDate().isAfter(period.end()) || added >= 12) {
                continue;
            }
            ObjectNode row = trades.addObject();
            row.put("date", trade.getTradeDate().toString());
            row.put("side", trade.getSide() == null ? "" : trade.getSide().toLowerCase());
            if (trade.getPrice() != null) row.put("price", Money.price(trade.getPrice()));
            if (trade.getShares() != null) row.put("shares", Money.price(trade.getShares()));
            if (trade.getRealizedPnl() != null) {
                row.put("realized_pnl", Money.amount(trade.getRealizedPnl()));
            }
            added++;
        }
        return payload;
    }

    // ==================== 数字底座（确定性） ====================

    /** 一个区间的统计快照：null 字段 = 算不出来（报告里写"样本不足"，不写 0）。 */
    record PeriodStats(int snapshotDays, BigDecimal returnPct, BigDecimal maxDrawdownPct,
                       BigDecimal flatRatioPct, BigDecimal excessVsBuyAndHoldPct,
                       int buys, int sells, int closedSells, BigDecimal realizedPnl,
                       Integer winRatePct) {

        ObjectNode toJson(ObjectMapper mapper) {
            ObjectNode node = mapper.createObjectNode();
            node.put("snapshot_days", snapshotDays);
            if (returnPct != null) node.put("return_pct", returnPct);
            if (maxDrawdownPct != null) node.put("max_drawdown_pct", maxDrawdownPct);
            if (flatRatioPct != null) node.put("flat_ratio_pct", flatRatioPct);
            if (excessVsBuyAndHoldPct != null) node.put("excess_vs_buy_and_hold_pct", excessVsBuyAndHoldPct);
            node.put("buys", buys);
            node.put("sells", sells);
            node.put("closed_sells", closedSells);
            if (realizedPnl != null) node.put("realized_pnl", realizedPnl);
            if (winRatePct != null) node.put("win_rate_pct", winRatePct);
            return node;
        }
    }

    /** 拼数字块（月度小结与季度信共用）。返回统计值供评语半环引用。 */
    private PeriodStats appendStatsBlock(StringBuilder text, Strategy strategy,
                                         PaperAccount account, Period period) {
        PeriodStats stats = computeStats(strategy.getId(), period);
        if (stats == null) {
            text.append("· 本期没有结算快照（策略这段没有在跑或结算未落快照），数字无从谈起。\n");
            text.append(accountLine(account));
            return null;
        }
        text.append("· 区间收益 ").append(sign(stats.returnPct())).append("%");
        if (stats.maxDrawdownPct() != null) {
            text.append("，最大回撤 ").append(stats.maxDrawdownPct()).append("%");
        }
        if (stats.flatRatioPct() != null) {
            text.append("，空仓占 ").append(stats.flatRatioPct()).append("% 的交易日");
        }
        text.append('\n');
        if (stats.excessVsBuyAndHoldPct() != null) {
            text.append("· 同期买入持有对比：超额 ").append(sign(stats.excessVsBuyAndHoldPct()))
                    .append("%（负超额就是「没动/慢半拍」的代价）\n");
        }
        text.append("· 成交：买 ").append(stats.buys()).append(" 笔、卖 ").append(stats.sells())
                .append(" 笔");
        if (stats.closedSells() > 0) {
            text.append("；已平仓 ").append(stats.closedSells()).append(" 笔的胜率 ")
                    .append(stats.winRatePct() == null ? "n/a" : stats.winRatePct()).append("%")
                    .append("，落袋 ").append(Money.amount(stats.realizedPnl()));
        } else if (stats.sells() > 0) {
            text.append("（卖出均未走完判读周期，胜率还谈不上）");
        }
        text.append('\n');
        text.append(accountLine(account)).append('\n');
        return stats;
    }

    /**
     * 区间统计。基期取窗口起点前**最后一个快照**（没有它，"9 月收益"会把 8 月 31 日
     * 到 9 月 1 日的跳变算进来 —— 月份的界就没了）。
     */
    PeriodStats computeStats(Long strategyId, Period period) {
        List<PaperEquitySnapshot> all = paperEquitySnapshotRepository
                .findByStrategyIdOrderByTradeDateAsc(strategyId);
        List<PaperEquitySnapshot> windowed = new ArrayList<>();
        for (PaperEquitySnapshot snap : all) {
            if (snap.getTradeDate() != null && snap.getTradeDate().isBefore(period.start())) {
                windowed.clear();          // 保留"起点前最后一个"作为基期
                windowed.add(snap);
            } else if (snap.getTradeDate() != null && !snap.getTradeDate().isAfter(period.end())) {
                windowed.add(snap);
            }
        }
        boolean anyInWindow = windowed.stream()
                .anyMatch(s -> !s.getTradeDate().isBefore(period.start()));
        if (!anyInWindow) {
            return null;   // 窗口内一格都没有：本期没在跑（基期不算数，它只是界标）
        }
        PaperEquitySeries.Summary summary = PaperEquitySeries.summarize(windowed);

        int buys = 0;
        int sells = 0;
        int closedSells = 0;
        int wins = 0;
        BigDecimal realized = BigDecimal.ZERO;
        boolean anyRealized = false;
        for (PaperTrade trade : paperTradeRepository
                .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategyId)) {
            if (trade.getTradeDate() == null || trade.getTradeDate().isBefore(period.start())
                    || trade.getTradeDate().isAfter(period.end())) {
                continue;
            }
            String side = trade.getSide() == null ? "" : trade.getSide().toLowerCase();
            if ("buy".equals(side)) {
                buys++;
            } else if ("sell".equals(side)) {
                sells++;
                if (trade.getRealizedPnl() != null) {
                    closedSells++;
                    anyRealized = true;
                    realized = realized.add(trade.getRealizedPnl());
                    if (trade.getRealizedPnl().signum() > 0) {
                        wins++;
                    }
                }
            }
        }
        Integer winRate = closedSells > 0
                ? BigDecimal.valueOf(wins * 100L).divide(BigDecimal.valueOf(closedSells),
                        0, RoundingMode.HALF_UP).intValue()
                : null;
        return new PeriodStats(summary.days(),
                summary.hasPeriod() ? summary.returnPct() : null,
                summary.maxDrawdownPct(), summary.flatRatioPct(),
                summary.excessVsBuyAndHoldPct(),
                buys, sells, closedSells, anyRealized ? Money.amount(realized) : null, winRate);
    }

    /** 教训库一行：季度信里用户最该知道"机器自己学到的经验"长什么样了。 */
    private String lessonsLine(Strategy strategy) {
        try {
            Map<String, Object> counts = memoryLessonService.statusCounts(
                    strategy.getUser().getId());
            Object active = counts.get("active");
            Object retired = counts.get("retired");
            return "经验库：生效教训 " + (active == null ? 0 : active)
                    + " 条，累计退役 " + (retired == null ? 0 : retired)
                    + " 条（被后续战绩证伪的会自动退场）";
        } catch (Exception e) {
            return "经验库：暂不可读";
        }
    }

    private String accountLine(PaperAccount account) {
        if (account == null) {
            return "· 账户：暂无数据";
        }
        return "· 账户现值：净值 " + Money.equity(account.getEquity())
                + "，现金 " + Money.amount(account.getCash())
                + "，持仓 " + Money.price(account.getShares()) + " 股";
    }

    private static String sign(BigDecimal value) {
        if (value == null) {
            return "n/a";
        }
        BigDecimal shown = value.setScale(2, RoundingMode.HALF_UP);
        return (shown.signum() >= 0 ? "+" : "") + shown;
    }

    /** 推送摘要：手机上只给开头一小段，全文在消息中心。 */
    private static String snippet(String text) {
        String flat = text == null ? "" : text.replace("\n", " ");
        return flat.length() <= 120 ? flat : flat.substring(0, 120) + "…（详见消息中心）";
    }
}
