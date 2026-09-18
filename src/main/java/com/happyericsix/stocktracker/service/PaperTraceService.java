package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.PaperTradeTrace;
import com.happyericsix.stocktracker.repository.PaperTradeTraceRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.domain.PageRequest;
import org.springframework.stereotype.Service;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.time.LocalDateTime;
import java.util.List;
import java.util.Locale;
import java.util.Optional;
import java.util.concurrent.atomic.AtomicLong;

/**
 * 痕迹的写入策略（**确定性，不调模型**）。
 *
 * <h3>它只做三件事</h3>
 * <ol>
 *   <li><b>降噪</b>：哪一次结算值得留痕（见 {@link #shouldTrace}）。实时路径每 5 分钟一次，
 *       把每次"规则没成立"都留痕就是 48 行/日/策略 —— "点时间戳看为什么"会被噪声淹没；</li>
 *   <li><b>去重</b>：{@code dedupeKey} 让"结算重跑"与"同一根 bar 反复结算"不产生重复行
 *       （重复发生时累加 {@code repeatCount}）；</li>
 *   <li><b>防篡改戳</b>：{@code traceHash} 绑定这一行的关键字段。用途是**对账**，
 *       不是"可复算"——前复权数据会随除权变化，引擎版本也会演进。</li>
 * </ol>
 *
 * <h3>为什么它是 fail-open 的</h3>
 * 与 {@code MemoryFactService} 同一条纪律：结算结果已经算出来了，**写不进痕迹只是少一份记录**，
 * 绝不能让它回滚掉真实的成交与持仓。失败计数单独留着（{@code writeFailures}），
 * 让"痕迹静默失效"这件事本身可见 —— 一个悄悄不写痕迹的痕迹系统比没有更危险。
 */
@Service
public class PaperTraceService {

    private static final Logger log = LoggerFactory.getLogger(PaperTraceService.class);

    /**
     * 实时路径上**值得留痕**的阻塞型跳过：信号本来要动，却被执行条件挡住了。
     *
     * <p>刻意不包含 {@code rule_not_met} 与 {@code warmup}：它们的意思是"没有信号"，
     * 每 5 分钟重复一次，且没有任何状态变化 —— 那是噪声，不是信息。
     * 日线结算不受这个集合限制：每天一行是"那天的结论"，本来就只有一行。
     */
    private static final List<String> BLOCKING_SKIP_REASONS = List.of(
            ExecutionContract.SKIP_LIMIT_BLOCKED,
            ExecutionContract.SKIP_T1_BLOCKED,
            ExecutionContract.SKIP_INSUFFICIENT_CASH_FOR_ONE_LOT,
            ExecutionContract.SKIP_INSUFFICIENT_CASH,
            ExecutionContract.SKIP_INVALID_PRICE,
            ExecutionContract.SKIP_DATA_UNAVAILABLE,
            ExecutionContract.SKIP_NO_BAR,
            ExecutionContract.SKIP_MARKET_CLOSED,
            ExecutionContract.SKIP_SUSPENDED,
            ExecutionContract.SKIP_EX_DIVIDEND_DAY);

    private final PaperTradeTraceRepository repository;
    private final AtomicLong writeFailures = new AtomicLong();

    public PaperTraceService(PaperTradeTraceRepository repository) {
        this.repository = repository;
    }

    /** 痕迹写入失败次数（结算照常，只是少一份记录）。`/health` 与报告都读它。 */
    public long writeFailures() {
        return writeFailures.get();
    }

    /**
     * 一次结算该不该留痕。
     *
     * <p>规则是**代码判定的**，不是"由模型觉得值不值得说"：
     * 日线结算每天一行；实时路径只留成交与阻塞型跳过。
     */
    public static boolean shouldTrace(PaperTradeTrace trace) {
        if (trace == null) {
            return false;
        }
        String kind = trace.getSettlementKind();
        if (ExecutionContract.SETTLEMENT_DAILY.equals(kind)) {
            return true;
        }
        if (ExecutionContract.SETTLEMENT_REALTIME.equals(kind)) {
            if (ExecutionContract.DECISION_SKIP.equals(trace.getDecision())) {
                return isBlocking(trace.getSkipReason());
            }
            return true;   // 成交：任何口径下都值得留痕
        }
        return true;       // 口径不明（unknown_*）：宁可多留，也不要静默丢
    }

    /**
     * 这个跳过原因算不算"阻塞"。
     *
     * <p>{@code unknown_*} 一律算阻塞：出现了不认识的原因，正是最该看见的时候 ——
     * 判成"不阻塞"会让它从痕迹里彻底消失。
     */
    public static boolean isBlocking(String skipReason) {
        if (skipReason == null || skipReason.isBlank()) {
            return false;
        }
        if (skipReason.startsWith(ExecutionContract.UNKNOWN_PREFIX)) {
            return true;
        }
        if (ExecutionContract.SKIP_RULE_NOT_MET.equals(skipReason)
                || ExecutionContract.SKIP_WARMUP.equals(skipReason)) {
            return false;
        }
        return BLOCKING_SKIP_REASONS.contains(skipReason);
    }

    /**
     * 去重键：**"同一件事"的定义只在这一处**。
     *
     * <ul>
     *   <li>日线：{@code daily:<strategy>:<tradeDate>} —— 每个交易日一行，结算重跑不会多写；</li>
     *   <li>成交：{@code realtime:<strategy>:<barTime>:<decision>} —— 同一根 bar 反复结算幂等；</li>
     *   <li>阻塞型跳过：{@code realtime:<strategy>:<tradeDate>:<skipReason>} ——
     *       一天的"T+1 挡着"只写一行并累加次数，而不是每 5 分钟一行。</li>
     * </ul>
     */
    public static String dedupeKey(PaperTradeTrace trace) {
        Long strategyId = trace.getStrategy() == null ? null : trace.getStrategy().getId();
        String kind = trace.getSettlementKind() == null ? "" : trace.getSettlementKind();
        if (ExecutionContract.SETTLEMENT_DAILY.equals(kind)) {
            return "daily:" + strategyId + ":" + trace.getTradeDate();
        }
        if (ExecutionContract.DECISION_SKIP.equals(trace.getDecision())) {
            return kind + ":" + strategyId + ":" + trace.getTradeDate() + ":"
                    + (trace.getSkipReason() == null ? "" : trace.getSkipReason());
        }
        return kind + ":" + strategyId + ":"
                + (trace.getBarTime() == null ? trace.getTradeDate() : trace.getBarTime())
                + ":" + trace.getDecision();
    }

    /**
     * 写入（或按去重键更新）一条痕迹，返回落库后的行（失败返回 {@code null}）。
     *
     * <p>重复出现时**更新**那一行而不是再写一行：痕迹该回答的是"这个键上的结论是什么"，
     * 而不是"它被算过几次"——后者由 {@code repeatCount} 回答。
     * 更新而不是保留首次，是因为首次可能是失败（取数不可用），
     * 而当天稍后成功结算时，读者要看到的是**成功那次**的结论。
     */
    public PaperTradeTrace record(PaperTradeTrace trace) {
        try {
            if (!shouldTrace(trace)) {
                return null;
            }
            String key = dedupeKey(trace);
            trace.setDedupeKey(key);
            trace.setTraceHash(traceHash(trace));

            Optional<PaperTradeTrace> existing = repository.findByDedupeKey(key);
            if (existing.isPresent()) {
                PaperTradeTrace row = existing.get();
                row.setDecision(trace.getDecision());
                row.setSkipReason(trace.getSkipReason());
                row.setSignal(trace.getSignal());
                row.setMatchedConditions(trace.getMatchedConditions());
                row.setTrigger(trace.getTrigger());
                row.setBarTime(trace.getBarTime());
                row.setBarDate(trace.getBarDate());
                row.setSymbol(trace.getSymbol());
                row.setAccount(trace.getAccount());
                row.setFillBasis(trace.getFillBasis());
                row.setBarClose(trace.getBarClose());
                row.setCashBefore(trace.getCashBefore());
                row.setSharesBefore(trace.getSharesBefore());
                row.setAvgCostBefore(trace.getAvgCostBefore());
                row.setEquityBefore(trace.getEquityBefore());
                row.setCashAfter(trace.getCashAfter());
                row.setSharesAfter(trace.getSharesAfter());
                row.setAvgCostAfter(trace.getAvgCostAfter());
                row.setEquityAfter(trace.getEquityAfter());
                row.setEngineVersion(trace.getEngineVersion());
                row.setAdjustMode(trace.getAdjustMode());
                row.setMoneyPolicyVersion(trace.getMoneyPolicyVersion());
                row.setSnapshotJson(trace.getSnapshotJson());
                row.setSnapshotSchemaVersion(trace.getSnapshotSchemaVersion());
                row.setTraceHash(trace.getTraceHash());
                row.setRepeatCount((row.getRepeatCount() == null ? 1 : row.getRepeatCount()) + 1);
                row.setLastSeenAt(LocalDateTime.now());
                return repository.save(row);
            }
            if (trace.getRepeatCount() == null) {
                trace.setRepeatCount(1);
            }
            return repository.save(trace);
        } catch (Exception e) {
            writeFailures.incrementAndGet();
            log.warn("痕迹写入失败 strategyId={} key={}: {}",
                    trace == null || trace.getStrategy() == null ? null : trace.getStrategy().getId(),
                    trace == null ? null : trace.getDedupeKey(), e.getMessage());
            return null;
        }
    }

    /**
     * 防篡改 / 对账戳：把这一行的**关键字段**串成一条规范串再取 sha256。
     *
     * <p>为什么不是"整行 JSON"：字段顺序、空值写法、时间格式都会变，那样这个戳会天天变，
     * 失去对账价值。这里只取"结算结论所依赖的那些值"，且金额用 {@code toPlainString()}，
     * 保证同一个数值永远得到同一个戳。
     */
    public static String traceHash(PaperTradeTrace trace) {
        if (trace == null) {
            return null;
        }
        StringBuilder canonical = new StringBuilder();
        append(canonical, trace.getStrategy() == null ? null : trace.getStrategy().getId());
        append(canonical, trace.getSettlementKind());
        append(canonical, trace.getTradeDate());
        append(canonical, trace.getBarTime());
        append(canonical, trace.getDecision());
        append(canonical, trace.getSkipReason());
        append(canonical, trace.getFillBasis());
        append(canonical, plain(trace.getBarClose()));
        append(canonical, plain(trace.getCashBefore()));
        append(canonical, plain(trace.getSharesBefore()));
        append(canonical, plain(trace.getEquityBefore()));
        append(canonical, plain(trace.getCashAfter()));
        append(canonical, plain(trace.getSharesAfter()));
        append(canonical, plain(trace.getEquityAfter()));
        append(canonical, trace.getEngineVersion());
        append(canonical, trace.getAdjustMode());
        append(canonical, trace.getMoneyPolicyVersion());
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] bytes = digest.digest(canonical.toString().getBytes(StandardCharsets.UTF_8));
            StringBuilder hex = new StringBuilder(bytes.length * 2);
            for (byte b : bytes) {
                hex.append(String.format(Locale.ROOT, "%02x", b));
            }
            return hex.toString();
        } catch (Exception e) {
            log.warn("痕迹摘要计算失败: {}", e.getMessage());
            return null;
        }
    }

    private static void append(StringBuilder target, Object value) {
        if (target.length() > 0) {
            target.append('|');
        }
        target.append(value == null ? "" : value.toString());
    }

    private static String plain(java.math.BigDecimal value) {
        return value == null ? null : value.toPlainString();
    }

    // ==================== 读取（报告与界面用） ====================

    public List<PaperTradeTrace> listRecent(Long strategyId, int limit) {
        int size = Math.max(1, Math.min(limit, 200));
        return repository.findByStrategyIdOrderByCreatedAtDesc(strategyId, PageRequest.of(0, size));
    }

    public List<PaperTradeTrace> listForDay(Long strategyId, java.time.LocalDate tradeDate) {
        return repository.findByStrategyIdAndTradeDateOrderByCreatedAtAsc(strategyId, tradeDate);
    }

    public List<PaperTradeTrace> listBySkipReason(Long strategyId, String skipReason) {
        return repository.findByStrategyIdAndSkipReasonOrderByCreatedAtDesc(
                strategyId, ExecutionContract.normalizeSkipReason(skipReason));
    }

    public long count(Long strategyId) {
        return repository.countByStrategyId(strategyId);
    }
}
