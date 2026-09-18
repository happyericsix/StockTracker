package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.entity.PaperEquitySnapshot;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.time.LocalDate;
import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * 预期登记与回填：把"这条策略接下来会怎样"变成**到期就能验的承诺**。
 *
 * <h3>它解决的是哪一类问题</h3>
 * 现在系统里所有关于未来的话都是不可验的：模型说"HOLD，等量能确认"、人说"这条规则该改改" ——
 * 说得好不好，事后没人算过账。于是"不表态"永远是安全策略（上一轮讨论过的那件事）。
 * 预期把不表态也变成**一个会被判错的承诺**：
 * 登记时写清度量、门槛与到期日；到期后由确定性代码回填实际值，达成与否一目了然。
 *
 * <h3>为什么复用客观事实通道，而不是新建一张表</h3>
 * <ol>
 *   <li><b>取代链天然给出历史</b>：同一批键上新写一次就取代旧的，
 *       "上一次预期是什么、有没有达成"是一次历史查询（{@code factHistory}）；</li>
 *   <li><b>模型读得到</b>：事实通道本来就在检索范围内，
 *       而写在对话里的承诺下一轮就找不到了；</li>
 *   <li><b>零新增</b>：没有新表、新 API、新权限、新迁移 —— 键加进白名单即可。</li>
 * </ol>
 *
 * <h3>三条纪律</h3>
 * <ul>
 *   <li><b>度量是封闭集</b>：只接受 {@link PaperEquitySeries#METRICS} 里的量
 *       （超额 / 收益 / 回撤 —— 都是账户层面可观测值，**不是股价预测**）。
 *       认不出的度量直接拒绝登记：一个算不出来的预期比没有预期更糟
 *       （它会在到期那天悄悄消失）；</li>
 *   <li><b>同一时刻只有一条有效预期</b>：登记新的即取代旧的（取代链留痕）；</li>
 *   <li><b>算不出来就不写结论</b>：样本不足时状态是 {@code unmeasurable}，
 *       而不是"未达成"，也不是 0 —— 三者的含义完全不同。</li>
 * </ul>
 */
@Service
public class ExpectationService {

    private static final Logger log = LoggerFactory.getLogger(ExpectationService.class);

    public static final String STATUS_PENDING = "pending";
    public static final String STATUS_MET = "met";
    public static final String STATUS_UNMET = "unmet";
    /** 到期了但算不出来（样本不足）。**不是**未达成：前者说明我们没数据，后者说明策略没做到。 */
    public static final String STATUS_UNMEASURABLE = "unmeasurable";

    public static final List<String> STATUSES =
            List.of(STATUS_PENDING, STATUS_MET, STATUS_UNMET, STATUS_UNMEASURABLE);

    /** 默认跨度：20 个交易日 ≈ 一个月的自然日。与"沉默期"同一个量级，便于和它对照。 */
    public static final int DEFAULT_HORIZON_DAYS = 28;
    private static final int MAX_HORIZON_DAYS = 400;

    private static final List<String> KEYS = List.of(
            ObjectiveFactKeys.EXPECTATION_AT,
            ObjectiveFactKeys.EXPECTATION_METRIC,
            ObjectiveFactKeys.EXPECTATION_THRESHOLD,
            ObjectiveFactKeys.EXPECTATION_DEADLINE,
            ObjectiveFactKeys.EXPECTATION_STATUS,
            ObjectiveFactKeys.EXPECTATION_OUTCOME,
            ObjectiveFactKeys.EXPECTATION_EVALUATED_AT);

    private final MemoryFactService memoryFactService;
    private final MemoryService memoryService;

    public ExpectationService(MemoryFactService memoryFactService, MemoryService memoryService) {
        this.memoryFactService = memoryFactService;
        this.memoryService = memoryService;
    }

    /** 一条预期（读视图）。 */
    public record Expectation(LocalDate registeredAt, String metric, BigDecimal threshold,
                              LocalDate deadline, String status, BigDecimal outcome,
                              LocalDateTime evaluatedAt) {

        public boolean isPending() {
            return status == null || STATUS_PENDING.equals(status);
        }
    }

    /**
     * 登记一条预期（取代同一策略上旧的预期）。
     *
     * @param metric       封闭集里的度量；不在集合里直接拒绝
     * @param threshold    门槛（实际 ≥ 门槛记为达成）
     * @param horizonDays  跨度（自然日；交易日近似 —— 没有交易日历是已知缺口，见设计 §7.1 P-5）
     */
    public Expectation register(Long userId, Long strategyId, String metric, BigDecimal threshold,
                                Integer horizonDays) {
        if (userId == null || strategyId == null || !PaperEquitySeries.isMetric(metric)
                || threshold == null) {
            return null;
        }
        int horizon = horizonDays == null ? DEFAULT_HORIZON_DAYS
                : Math.max(1, Math.min(horizonDays, MAX_HORIZON_DAYS));
        LocalDate today = LocalDate.now(ZoneId.of("Asia/Shanghai"));
        LocalDate deadline = today.plusDays(horizon);
        LocalDateTime now = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));

        Map<String, String> values = new LinkedHashMap<>();
        values.put(ObjectiveFactKeys.EXPECTATION_AT, now.withNano(0).toString());
        values.put(ObjectiveFactKeys.EXPECTATION_METRIC, metric.trim());
        values.put(ObjectiveFactKeys.EXPECTATION_THRESHOLD, plain(threshold));
        values.put(ObjectiveFactKeys.EXPECTATION_DEADLINE, deadline.toString());
        values.put(ObjectiveFactKeys.EXPECTATION_STATUS, STATUS_PENDING);
        // 上一次的实际值/回填时间**刻意不清空**：它们属于上一次那条预期，会被取代链留着；
        // 写空字符串反而会让"值没变、取代链却多一条"的假变更出现在这里。
        if (write(userId, strategyId, values, now)) {
            log.info("Registered expectation strategyId={} metric={} threshold={} deadline={}",
                    strategyId, metric, threshold, deadline);
            return new Expectation(today, metric.trim(), threshold, deadline, STATUS_PENDING,
                    null, null);
        }
        return null;
    }

    /**
     * 到期就回填（每日结算后调用一次）。
     *
     * <p>失败**吞掉**：与客观事实通道同一条纪律 —— 预期回填是旁路，
     * 绝不能因为记忆写不进去而影响结算。
     *
     * @return 这一次真的回填了的那条（没到期/算不出来/已回填则返回 null）
     */
    public Expectation evaluate(Long userId, Long strategyId, List<PaperEquitySnapshot> series,
                                LocalDate asOf) {
        try {
            Expectation expectation = latest(userId, strategyId);
            if (expectation == null || expectation.registeredAt() == null
                    || !expectation.isPending() || expectation.deadline() == null) {
                return null;
            }
            LocalDate today = asOf == null ? LocalDate.now(ZoneId.of("Asia/Shanghai")) : asOf;
            if (today.isBefore(expectation.deadline())) {
                return null;      // 还没到期：**留 pending**，不提前下结论
            }
            BigDecimal outcome = PaperEquitySeries.metricValue(series, expectation.metric(),
                    expectation.registeredAt());
            LocalDate current = LocalDate.now(ZoneId.of("Asia/Shanghai"));
            LocalDateTime now = LocalDateTime.now(ZoneId.of("Asia/Shanghai"));

            Map<String, String> values = new LinkedHashMap<>();
            values.put(ObjectiveFactKeys.EXPECTATION_METRIC, expectation.metric());
            values.put(ObjectiveFactKeys.EXPECTATION_THRESHOLD, plain(expectation.threshold()));
            values.put(ObjectiveFactKeys.EXPECTATION_AT, expectation.registeredAt() + "T00:00:00");
            values.put(ObjectiveFactKeys.EXPECTATION_DEADLINE, expectation.deadline().toString());
            values.put(ObjectiveFactKeys.EXPECTATION_STATUS, STATUS_UNMEASURABLE);
            if (outcome != null) {
                values.put(ObjectiveFactKeys.EXPECTATION_OUTCOME, plain(outcome));
                values.put(ObjectiveFactKeys.EXPECTATION_STATUS,
                        outcome.compareTo(expectation.threshold()) >= 0 ? STATUS_MET : STATUS_UNMET);
            }
            values.put(ObjectiveFactKeys.EXPECTATION_EVALUATED_AT, now.withNano(0).toString());
            if (!write(userId, strategyId, values, now)) {
                return null;
            }
            Expectation result = new Expectation(expectation.registeredAt(), expectation.metric(),
                    expectation.threshold(), expectation.deadline(),
                    values.get(ObjectiveFactKeys.EXPECTATION_STATUS), outcome, now);
            log.info("Evaluated expectation strategyId={} metric={} status={} outcome={}",
                    strategyId, result.metric(), result.status(), outcome);
            return result;
        } catch (Exception e) {
            log.warn("预期回填失败 strategyId={}: {}", strategyId, e.getMessage());
            return null;
        }
    }

    /** 读回当前有效的预期（没有则 null）。"没有记录"与"记录为空"必须分开。 */
    public Expectation latest(Long userId, Long strategyId) {
        if (memoryFactService == null || userId == null || strategyId == null) {
            return null;
        }
        try {
            Map<String, String> values = memoryFactService.activeFactValues(
                    userId, ObjectiveFactKeys.strategySubject(strategyId), KEYS);
            if (!values.containsKey(ObjectiveFactKeys.EXPECTATION_METRIC)
                    || !values.containsKey(ObjectiveFactKeys.EXPECTATION_THRESHOLD)) {
                return null;
            }
            return new Expectation(
                    parseDate(values.get(ObjectiveFactKeys.EXPECTATION_AT)),
                    values.get(ObjectiveFactKeys.EXPECTATION_METRIC),
                    parseDecimal(values.get(ObjectiveFactKeys.EXPECTATION_THRESHOLD)),
                    parseDate(values.get(ObjectiveFactKeys.EXPECTATION_DEADLINE)),
                    values.getOrDefault(ObjectiveFactKeys.EXPECTATION_STATUS, STATUS_PENDING),
                    parseDecimal(values.get(ObjectiveFactKeys.EXPECTATION_OUTCOME)),
                    parseDateTime(values.get(ObjectiveFactKeys.EXPECTATION_EVALUATED_AT)));
        } catch (Exception e) {
            log.warn("读取预期失败 strategyId={}: {}", strategyId, e.getMessage());
            return null;
        }
    }

    /** 写事实（复用客观事实通道的既有入口：provenance/trust/confirmed 的标注都在那里）。 */
    private boolean write(Long userId, Long strategyId, Map<String, String> values,
                          LocalDateTime dataAsOf) {
        if (memoryFactService == null || memoryService == null) {
            return false;
        }
        String subject = ObjectiveFactKeys.strategySubject(strategyId);
        if (subject.isBlank()) {
            return false;
        }
        List<MemoryFactRequest> facts = new ArrayList<>();
        for (Map.Entry<String, String> entry : values.entrySet()) {
            MemoryFactRequest fact = ObjectiveFactKeys.observation(
                    subject, entry.getKey(), entry.getValue(), dataAsOf);
            if (fact != null) {
                facts.add(fact);
            }
        }
        if (facts.isEmpty()) {
            return false;
        }
        memoryFactService.recordObjective(userId, memoryService.currentSessionKey(userId), facts);
        return true;
    }

    /** 给报告与界面用的一句话（确定性，不调模型）。 */
    public static String describe(Expectation expectation) {
        if (expectation == null) {
            return "";
        }
        String threshold = plain(expectation.threshold());
        String metric = label(expectation.metric());
        if (expectation.isPending()) {
            return String.format("已登记预期：到 %s 为止，%s ≥ %s（尚未到期）",
                    expectation.deadline(), metric, threshold);
        }
        if (STATUS_UNMEASURABLE.equals(expectation.status())) {
            return String.format("预期到 %s 到期，但**算不出来**（净值样本不足）——这不等于未达成",
                    expectation.deadline());
        }
        String outcome = expectation.outcome() == null ? "n/a" : plain(expectation.outcome());
        return String.format("预期%s：到 %s，%s 实际 %s（门槛 %s）",
                STATUS_MET.equals(expectation.status()) ? "**达成**" : "**未达成**",
                expectation.deadline(), metric, outcome, threshold);
    }

    /** 度量的中文名（界面与报告共用一份措辞，不两处各写一遍）。 */
    public static String label(String metric) {
        if (metric == null) {
            return "度量";
        }
        return switch (metric.trim()) {
            case PaperEquitySeries.METRIC_EXCESS_VS_BUY_AND_HOLD -> "相对买入持有的超额";
            case PaperEquitySeries.METRIC_RETURN -> "账户收益";
            case PaperEquitySeries.METRIC_MAX_DRAWDOWN -> "最大回撤";
            default -> metric;
        };
    }

    /** 值的字符串化：与事实通道的口径一致（不要科学计数法、不要多余小数位）。 */
    private static String plain(BigDecimal value) {
        if (value == null) {
            return "";
        }
        return value.setScale(4, RoundingMode.HALF_UP).stripTrailingZeros().toPlainString();
    }

    private static BigDecimal parseDecimal(String raw) {
        try {
            return raw == null || raw.isBlank() ? null : new BigDecimal(raw.trim());
        } catch (NumberFormatException e) {
            return null;
        }
    }

    private static LocalDate parseDate(String raw) {
        try {
            if (raw == null || raw.isBlank()) {
                return null;
            }
            String text = raw.trim();
            return LocalDate.parse(text.length() > 10 ? text.substring(0, 10) : text);
        } catch (Exception e) {
            return null;
        }
    }

    private static LocalDateTime parseDateTime(String raw) {
        try {
            return raw == null || raw.isBlank() ? null
                    : LocalDateTime.parse(raw.length() > 19 ? raw.substring(0, 19) : raw.trim());
        } catch (Exception e) {
            return null;
        }
    }
}
