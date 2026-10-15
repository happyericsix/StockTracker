package com.happyericsix.stocktracker.service;

import com.github.benmanes.caffeine.cache.Cache;
import com.github.benmanes.caffeine.cache.Caffeine;
import com.happyericsix.stocktracker.dto.StockHistoryResponse;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.Duration;
import java.time.LocalDate;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 决策质量对账：**每一笔成交，N 个交易日之后回头看，方向对了吗？**
 *
 * <h3>这个类现在是什么（2026-10-14 收窄）</h3>
 * 原「智囊 vs 规则」对比面板（{@code quality()}，按决策源分组比胜率）已删除：
 * 规则与 agent 是同一条流水线的层——规则出信号、委员会划授权边界、边界内执行——
 * 不是两个可对比的决策者；且成交上的 decision_mode 标签会把"授权内的规则单"
 * 记成 agent，按来源分组测不出干净的东西。
 *
 * <p>留下的是**对账机制本身**（单笔方向对错，N 交易日回看）：
 * <ul>
 *   <li>{@link #strategyExperience}：单策略经验包——委员会每次开会前读"自己的病历"；</li>
 *   <li>{@link #agentOutcomeSince}：教训退役——按后续战绩判断一条经验教训该不该下架。</li>
 * </ul>
 * 按策略（而非按决策源）对答案的界面出口在策略详情页的「决策日志」
 * （{@code TradeJournalService}），与这里口径互补、各管一段。
 */
@Service
public class DecisionQualityService {
    private static final Logger log = LoggerFactory.getLogger(DecisionQualityService.class);

    private static final int DEFAULT_HORIZON = 5;
    private static final int DEFAULT_LOOKBACK_DAYS = 60;

    private final PaperTradeRepository paperTradeRepository;
    private final PaperTradeTraceRepository paperTradeTraceRepository;
    private final StockDataGateway stockDataGateway;

    /** 日线收盘缓存：一次对账要对同一只票的几十笔成交各查一次历史，不缓存就是自残。 */
    private final Cache<String, List<CloseOnDate>> closes = Caffeine.newBuilder()
            .expireAfterWrite(Duration.ofHours(1))
            .maximumSize(64)
            .build();

    public DecisionQualityService(PaperTradeRepository paperTradeRepository,
                                  PaperTradeTraceRepository paperTradeTraceRepository,
                                  StockDataGateway stockDataGateway) {
        this.paperTradeRepository = paperTradeRepository;
        this.paperTradeTraceRepository = paperTradeTraceRepository;
        this.stockDataGateway = stockDataGateway;
    }

    // ==================== 单策略经验包（agent 互通 P4，经验回流） ====================

    /** 经验包的开场白：让模型明确知道这是"自己过去的成绩"，先读病历再读盘。 */
    private static final String EXPERIENCE_NOTE_PREFIX =
            "以下是这条策略自己过去的成交成绩单（开会前先读自己的病历）。";

    /**
     * 单条策略的**经验包**：这条策略自己过去的成交成绩单，随决策请求进委员会/盯盘的
     * 证据包 —— 修的是"委员会每次开会都从零读盘，5 日对账发现的错误模式永远到不了
     * 下一次决策"这个断路。数据全部来自已落库的成交与日线（确定性、零 LLM 成本）。
     *
     * <p>口径与 {@link #quality} 完全一致（N 个交易日后回看、方向对齐、pending 不进
     * 统计），只是范围从"用户全部成交"收窄到"这条策略的成交"。recent 只列**已判读**
     * 的成交 —— 老手要的是结论（对/错、错多少），不是流水；还没走满窗口的只报个数。
     *
     * @return null = 这条策略近窗口内没有任何成交（调用方不带字段，Python 侧 fail-open）
     */
    public Map<String, Object> strategyExperience(Strategy strategy, int maxRecent) {
        if (strategy == null || strategy.getId() == null) {
            return null;
        }
        LocalDate since = LocalDate.now().minusDays(DEFAULT_LOOKBACK_DAYS);
        List<PaperTrade> trades = paperTradeRepository
                .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId());

        int evaluated = 0;
        int wins = 0;
        // alignedPct*100*100 的整数累加（百万分之一精度），与 bump 的防浮点误差手法同源
        long alignedMicro = 0;
        int pending = 0;
        List<Map<String, Object>> recent = new ArrayList<>();

        for (PaperTrade trade : trades) {
            if (trade.getTradeDate() == null || trade.getTradeDate().isBefore(since)) {
                continue;
            }
            BigDecimal entry = trade.getPrice();
            String side = trade.getSide() == null ? "" : trade.getSide().toLowerCase();
            if (entry == null || entry.signum() <= 0) {
                continue;
            }
            Double fwd = forwardReturn(trade.getSymbol(), trade.getTradeDate(), DEFAULT_HORIZON);
            if (fwd == null) {
                pending++;
                continue;
            }
            double aligned = "sell".equals(side) ? -fwd : fwd;
            evaluated++;
            if (aligned > 0) {
                wins++;
            }
            alignedMicro += Math.round(aligned * 100.0 * 100.0);
            if (recent.size() < Math.max(1, maxRecent)) {
                Map<String, Object> row = new LinkedHashMap<>();
                row.put("date", trade.getTradeDate().toString());
                row.put("side", side);
                row.put("mode", traceModeOf(trade));
                row.put("alignedPct", round(aligned * 100.0));
                row.put("win", aligned > 0);
                if ("sell".equals(side)) {
                    // 已了结的往返当时就封口了，不受 5 日对账窗口影响 —— 路径口径随行带上
                    Map<String, Object> rt = roundTripOf(trade);
                    if (rt != null) {
                        row.putAll(rt);
                    }
                }
                recent.add(row);
            }
        }

        // 已了结卖出的路径聚合（MAE/MFE）：窗口内全部卖出都算（含对账还 pending 的），
        // 与明细行同一套口径 —— 聚合与明细对不上，委员会就该不信这个包了。
        int trips = 0;
        int pathCount = 0;
        long roundTripMicro = 0;
        long mfeMicro = 0;
        long maeMicro = 0;
        int exitEarlyCount = 0;
        int deepAdverseCount = 0;
        for (PaperTrade trade : trades) {
            if (trade.getTradeDate() == null || trade.getTradeDate().isBefore(since)) {
                continue;
            }
            Map<String, Object> rt = roundTripOf(trade);
            if (rt == null || !(rt.get("roundTripPct") instanceof Number rtPct)) {
                continue;
            }
            trips++;
            roundTripMicro += Math.round(rtPct.doubleValue() * 100.0);
            if (rt.get("mfePct") instanceof Number mfeV) {
                pathCount++;
                mfeMicro += Math.round(mfeV.doubleValue() * 100.0);
                maeMicro += Math.round(((Number) rt.get("maePct")).doubleValue() * 100.0);
            }
            if (Boolean.TRUE.equals(rt.get("exitEarly"))) {
                exitEarlyCount++;
            }
            if (Boolean.TRUE.equals(rt.get("deepAdverse"))) {
                deepAdverseCount++;
            }
        }

        if (evaluated == 0) {
            // 有成交但都还没走满窗口：给最小包 —— 委员会至少该知道自己交易过、还无法对账
            if (pending == 0) {
                return null;
            }
            Map<String, Object> waiting = new LinkedHashMap<>();
            waiting.put("note", EXPERIENCE_NOTE_PREFIX + "近期有 " + pending
                    + " 笔成交，都还没走满 " + DEFAULT_HORIZON + " 个交易日，暂无已判读成绩。");
            waiting.put("pendingTrades", pending);
            return waiting;
        }

        Map<String, Object> out = new LinkedHashMap<>();
        String note = EXPERIENCE_NOTE_PREFIX
                + "统计口径：每笔成交 " + DEFAULT_HORIZON + " 个交易日后回看方向对错"
                + "（买入看涨对、卖出看跌对）；还没走满窗口的不计入。"
                + "这是历史事实，供决策参考，不是对未来走势的判断。";
        if (trips > 0) {
            note += "已了结卖出另附路径口径：maePct/mfePct = 持仓期间日线收盘相对 FIFO 入场"
                    + "成本的最大不利/有利偏移（%，只用持仓窗口内的 bar，不含盘中极值）；"
                    + "exitEarly = 曾有 ≥" + EXIT_EARLY_MFE_MIN_PCT + "% 浮盈但落袋不足一半；"
                    + "deepAdverse = 持仓期曾浮亏 ≥" + Math.abs(DEEP_ADVERSE_MAE_PCT) + "%。";
        }
        out.put("note", note);
        out.put("evaluatedTrades", evaluated);
        out.put("winRatePct", round(wins * 100.0 / evaluated));
        out.put("avgAlignedPct", round(alignedMicro / 100.0 / evaluated));
        out.put("pendingTrades", pending);
        out.put("recent", recent);
        if (trips > 0) {
            Map<String, Object> roundTrips = new LinkedHashMap<>();
            roundTrips.put("count", trips);
            roundTrips.put("avgRoundTripPct", round(roundTripMicro / 100.0 / trips));
            if (pathCount > 0) {
                roundTrips.put("avgMfePct", round(mfeMicro / 100.0 / pathCount));
                roundTrips.put("avgMaePct", round(maeMicro / 100.0 / pathCount));
            }
            roundTrips.put("exitEarlyCount", exitEarlyCount);
            roundTrips.put("deepAdverseCount", deepAdverseCount);
            out.put("roundTrips", roundTrips);
        }
        return out;
    }

    // ==================== 教训退出机制（按后续战绩退役，2026-10-13） ====================

    /**
     * 这条策略在某日期**之后**的 agent 模式成交战绩（N 日后回看的已判读口径）。
     *
     * <p>用途是教训的**退出机制**：周反思的教训会随经验包无限期回流进委员会的
     * 提示词——一条坏教训没人退役，就永远污染决策。口径与
     * {@link #strategyExperience} 完全一致（方向对齐、pending 不进统计），只做两个收窄：
     * ① 只看 agent 模式成交（教训只进 agent 路径，规则成交不该替它背书或背锅）；
     * ② 只看 {@code since} 之后的成交（教训只对它出生之后的决策负责）。
     *
     * @return {evaluated, wins, winRatePct}；evaluated=0 表示还没有可判读的样本
     *         （调用方按"证据不足，不退役"处理，绝不在小样本上撤教训）。
     */
    public Map<String, Object> agentOutcomeSince(Strategy strategy, LocalDate since) {
        Map<String, Object> out = new LinkedHashMap<>();
        out.put("evaluated", 0);
        out.put("wins", 0);
        out.put("winRatePct", null);
        if (strategy == null || strategy.getId() == null || since == null) {
            return out;
        }
        int evaluated = 0;
        int wins = 0;
        for (PaperTrade trade : paperTradeRepository
                .findByStrategyIdOrderByTradeDateDescCreatedAtDesc(strategy.getId())) {
            if (trade.getTradeDate() == null || trade.getTradeDate().isBefore(since)) {
                continue;
            }
            if (!ExecutionContract.DECISION_MODE_AGENT.equals(traceModeOf(trade))) {
                continue;
            }
            BigDecimal entry = trade.getPrice();
            String side = trade.getSide() == null ? "" : trade.getSide().toLowerCase();
            if (entry == null || entry.signum() <= 0) {
                continue;
            }
            Double fwd = forwardReturn(trade.getSymbol(), trade.getTradeDate(), DEFAULT_HORIZON);
            if (fwd == null) {
                continue; // 还没走满窗口：pending 不进统计
            }
            double aligned = "sell".equals(side) ? -fwd : fwd;
            evaluated++;
            if (aligned > 0) {
                wins++;
            }
        }
        out.put("evaluated", evaluated);
        out.put("wins", wins);
        if (evaluated > 0) {
            out.put("winRatePct", round(wins * 100.0 / evaluated));
        }
        return out;
    }

    // ==================== 往返路径口径：MAE/MFE（2026-10-15）====================

    /** exitEarly 判定的最低浮盈门槛（%）：MFE 连这条都没到，谈不上"退出太早"。 */
    private static final double EXIT_EARLY_MFE_MIN_PCT = 2.0;

    /** exitEarly 判定的落袋比例：最好偏移的一半都没拿到，才算"把浮盈还回去"。 */
    private static final double EXIT_EARLY_KEEP_RATIO = 0.5;

    /** deepAdverse 判定的最深浮亏线（%）：持仓期曾浮亏超过这条线。 */
    private static final double DEEP_ADVERSE_MAE_PCT = -3.0;

    private static final ObjectMapper JSON = new ObjectMapper();

    /**
     * 一笔**已了结卖出**的往返路径：入场用的是它自己对冲的 FIFO 批次加权成本，
     * 路径用的是持仓窗口（最早批次 openedDate → 卖出日）内的**日线收盘**。
     *
     * <h3>口径（与 realizedPnl/forwardReturn 同一条纪律）</h3>
     * <ul>
     *   <li>入场成本 = Σ(批次 cost × 批次 shares) / Σ批次 shares —— 成交价口径、不含费，
     *       与 {@code PaperTradingService.consumeLotsFifoQuietly} 落毛利 realizedPnl 的基准一致；</li>
     *   <li>roundTripPct = 卖出价 / 入场成本 − 1（毛收益，与 realizedPnl 完全同口径）；</li>
     *   <li>maePct / mfePct = 窗口内最低/最高收盘相对入场成本的偏移（%）。**只用窗口内的
     *       bar**，卖出之后的数据一根都不看 —— 和 forwardReturn 一样，不存在未来函数；</li>
     *   <li>日线收盘口径（与决策同频），不含盘中极值 —— MAE/MFE 会被低估，宁可保守也不
     *       把和决策频率对不上的"机会"算进战绩；</li>
     *   <li>matchedLots 是 2026-10-15 上线的字段：更早的卖出没有批次明细，只能由
     *       realizedPnl 反推入场价（毛口径恒等式），此时**不给 maePct/mfePct** ——
     *       窗口起点不知道，宁缺毋滥，绝不拿"当日一个点"冒充路径。</li>
     * </ul>
     *
     * @return 只含算得出的键（算不出就不带，调用方 fail-open）；非卖出或缺关键字段返回 null
     */
    public Map<String, Object> roundTripOf(PaperTrade sell) {
        if (sell == null || sell.getPrice() == null || sell.getTradeDate() == null
                || sell.getSymbol() == null) {
            return null;
        }
        String side = sell.getSide() == null ? "" : sell.getSide().toLowerCase();
        if (!"sell".equals(side)) {
            return null;
        }

        BigDecimal entry = null;
        LocalDate openedDate = null;
        if (sell.getMatchedLots() != null && !sell.getMatchedLots().isBlank()) {
            try {
                JsonNode lots = JSON.readTree(sell.getMatchedLots());
                BigDecimal costShares = BigDecimal.ZERO; // Σ(cost × shares)
                BigDecimal shareSum = BigDecimal.ZERO;
                for (JsonNode lot : lots) {
                    if (lot == null || !lot.isObject()) {
                        continue;
                    }
                    BigDecimal c = new BigDecimal(lot.path("cost").asText("0"));
                    BigDecimal s = new BigDecimal(lot.path("shares").asText("0"));
                    if (c.signum() <= 0 || s.signum() <= 0) {
                        continue;
                    }
                    costShares = costShares.add(c.multiply(s));
                    shareSum = shareSum.add(s);
                    String opened = lot.path("openedDate").asText("");
                    if (!opened.isEmpty()) {
                        LocalDate d = LocalDate.parse(opened);
                        if (openedDate == null || d.isBefore(openedDate)) {
                            openedDate = d;
                        }
                    }
                }
                if (shareSum.signum() > 0) {
                    entry = costShares.divide(shareSum, 4, RoundingMode.HALF_UP);
                }
            } catch (Exception e) {
                log.debug("matchedLots parse failed for trade {}: {}",
                        sell.getId(), e.getMessage());
            }
        }
        if (entry == null || entry.signum() <= 0) {
            // 无批次明细的遗留卖出：毛口径恒等式 realizedPnl = (卖出价 − 入场价) × 股数 反推。
            // 入场日期不知道 → 没有 MAE/MFE，只有往返收益。
            if (sell.getRealizedPnl() != null && sell.getShares() != null
                    && sell.getShares().signum() > 0) {
                entry = sell.getPrice().subtract(
                        sell.getRealizedPnl().divide(sell.getShares(), 4, RoundingMode.HALF_UP));
            }
            if (entry == null || entry.signum() <= 0) {
                return null;
            }
        }

        Map<String, Object> out = new LinkedHashMap<>();
        out.put("entryPrice", round4(entry.doubleValue()));
        out.put("exitPrice", round4(sell.getPrice().doubleValue()));
        double roundTrip = sell.getPrice().doubleValue() / entry.doubleValue() - 1.0;
        out.put("roundTripPct", round(roundTrip * 100.0));

        Double mae = null;
        Double mfe = null;
        int bars = 0;
        if (openedDate != null) {
            List<CloseOnDate> series = closes.get(sell.getSymbol(), this::loadCloses);
            if (series != null) {
                double min = Double.MAX_VALUE;
                double max = -Double.MAX_VALUE;
                for (CloseOnDate c : series) {
                    if (c.date.isBefore(openedDate)) {
                        continue;
                    }
                    if (c.date.isAfter(sell.getTradeDate())) {
                        break; // 序列按日期升序：越过卖出日即可停，未来的 bar 一根不看
                    }
                    min = Math.min(min, c.close);
                    max = Math.max(max, c.close);
                    bars++;
                }
                if (bars > 0) {
                    mae = (min / entry.doubleValue() - 1.0) * 100.0;
                    mfe = (max / entry.doubleValue() - 1.0) * 100.0;
                }
            }
        }
        if (mae != null && mfe != null) {
            out.put("heldBars", bars);
            out.put("maePct", round(mae));
            out.put("mfePct", round(mfe));
            boolean exitEarly = mfe >= EXIT_EARLY_MFE_MIN_PCT
                    && roundTrip * 100.0 < mfe * EXIT_EARLY_KEEP_RATIO;
            boolean deepAdverse = mae <= DEEP_ADVERSE_MAE_PCT;
            if (exitEarly) {
                out.put("exitEarly", true);
            }
            if (deepAdverse) {
                out.put("deepAdverse", true);
            }
        }
        return out;
    }

    /** 成交的决策源：以痕迹为准（策略的 decisionMode 会被切换，历史成交不能跟着改写）。 */
    private String traceModeOf(PaperTrade trade) {
        if (trade.getTraceId() != null) {
            try {
                PaperTradeTrace trace = paperTradeTraceRepository.findById(trade.getTraceId())
                        .orElse(null);
                if (trace != null && trace.getDecisionMode() != null) {
                    return ExecutionContract.normalizeDecisionMode(trace.getDecisionMode());
                }
            } catch (Exception e) {
                log.debug("trace load failed for trade {}: {}", trade.getId(), e.getMessage());
            }
        }
        return ExecutionContract.normalizeDecisionMode(
                trade.getStrategy() == null ? null : trade.getStrategy().getDecisionMode());
    }

    /**
     * 前向收益：entry → N 个交易日后的收盘。数据缺失/未走满窗口返回 null（pending）。
     */
    private Double forwardReturn(String symbol, LocalDate tradeDate, int horizon) {
        if (symbol == null || tradeDate == null) {
            return null;
        }
        List<CloseOnDate> series = closes.get(symbol, this::loadCloses);
        if (series == null || series.isEmpty()) {
            return null;
        }
        int idx = -1;
        for (int i = 0; i < series.size(); i++) {
            if (!series.get(i).date.isBefore(tradeDate)) {
                idx = i; // 第一根 ≥ tradeDate 的 bar（成交日当日）
                break;
            }
        }
        if (idx < 0) {
            return null; // 成交日比历史窗口还早：这单太老，不评
        }
        int future = idx + horizon;
        if (future >= series.size()) {
            return null; // 还没走满 N 个交易日
        }
        double entryFuture = series.get(idx).close;
        double exit = series.get(future).close;
        return entryFuture > 0 ? exit / entryFuture - 1.0 : null;
    }

    private List<CloseOnDate> loadCloses(String symbol) {
        try {
            StockHistoryResponse resp = stockDataGateway.getStockHistory(symbol, "day");
            if (resp == null || resp.timeSeries() == null) {
                return List.of();
            }
            List<CloseOnDate> list = new ArrayList<>();
            for (Map.Entry<String, StockHistoryResponse.DailyPrice> e
                    : resp.timeSeries().entrySet()) {
                try {
                    LocalDate d = LocalDate.parse(e.getKey().substring(0, 10));
                    double close = Double.parseDouble(e.getValue().close());
                    if (close > 0) {
                        list.add(new CloseOnDate(d, close));
                    }
                } catch (Exception ignore) {
                    // 脏行跳过：一行坏数据不该让整只票没法对账
                }
            }
            list.sort(Comparator.comparing(c -> c.date));
            return list;
        } catch (Exception e) {
            log.warn("history load failed for {}: {}", symbol, e.getMessage());
            return List.of();
        }
    }

    private static double round(double v) {
        return Math.round(v * 100.0) / 100.0;
    }

    /** 价格口径 4 位小数（与 Money.price 的展示精度对齐）。 */
    private static double round4(double v) {
        return Math.round(v * 10000.0) / 10000.0;
    }

    private record CloseOnDate(LocalDate date, double close) {}
}
