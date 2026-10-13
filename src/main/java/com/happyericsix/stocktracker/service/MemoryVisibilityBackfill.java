package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.MemoryFactRequest;
import com.happyericsix.stocktracker.repository.MemoryFactRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.support.TransactionTemplate;

import java.time.LocalDateTime;
import java.time.ZoneId;
import java.util.List;
import java.util.Map;

/**
 * 双层记忆的存量回填（一次性，幂等，2026-10-09）。
 *
 * <p>{@code user_visible} 列上线时库里已有历史事实。新写入由
 * {@code ObjectiveFactKeys.observation()} 工厂直接标不可见；这里只补两件旧账：
 * <ol>
 *   <li>系统遥测谓词的历史行 → 不可见（它们不冒充"助手记住的我"）；</li>
 *   <li>每个有观测的策略 → 补一条聚合叙事（run_summary），让"策略结论"区
 *       对存量数据也有内容 —— 否则用户升级后看到的是"记忆全空了"。</li>
 * </ol>
 *
 * <p>失败绝不阻塞启动（与 {@link NewsCategoryBackfill} 同一条纪律）：
 * 记忆是增强层，回填没跑成只是显示层差一口气，下次启动会再试。
 */
@Component
public class MemoryVisibilityBackfill implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(MemoryVisibilityBackfill.class);
    private static final ZoneId ZONE_CN = ZoneId.of("Asia/Shanghai");

    private final MemoryFactRepository factRepo;
    private final MemoryFactService factService;
    private final TransactionTemplate transactionTemplate;

    public MemoryVisibilityBackfill(MemoryFactRepository factRepo,
                                    MemoryFactService factService,
                                    PlatformTransactionManager transactionManager) {
        this.factRepo = factRepo;
        this.factService = factService;
        this.transactionTemplate = new TransactionTemplate(transactionManager);
    }

    @Override
    public void run(ApplicationArguments args) {
        try {
            int hidden = transactionTemplate.execute(status -> factRepo.markTelemetryInvisible(
                    ObjectiveFactKeys.FACT_TYPE_OBSERVATION, ObjectiveFactKeys.TELEMETRY_PREDICATES));
            if (hidden > 0) {
                log.info("双层记忆回填：{} 条系统遥测事实标记为不可见", hidden);
            }
            backfillRunSummaries();
        } catch (Exception e) {
            log.warn("双层记忆回填失败（下次启动重试）：{}", e.toString());
        }
    }

    /**
     * 为每个没有 run_summary 的策略主体补一条聚合叙事（从取代链上的最新值拼）。
     * 幂等：已有一条有效 run_summary 的主体跳过 —— 叙事走取代链，
     * 之后新的回测/验证自然取代它，这里不重复生成。
     */
    private void backfillRunSummaries() {
        int created = 0;
        for (Object[] row : factRepo.findDistinctStrategySubjects()) {
            Long userId = (Long) row[0];
            String subject = String.valueOf(row[1]);
            try {
                if (!factRepo.findActiveByKey(userId, subject, ObjectiveFactKeys.RUN_SUMMARY).isEmpty()) {
                    continue;
                }
                String story = storyFromValues(factService.activeFactValues(userId, subject,
                        List.of(
                                ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT,
                                ObjectiveFactKeys.BACKTEST_BUY_AND_HOLD_RETURN_PCT,
                                ObjectiveFactKeys.BACKTEST_MAX_DRAWDOWN_PCT,
                                ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT,
                                ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT,
                                ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT,
                                ObjectiveFactKeys.PAPER_RETURN_PCT)));
                MemoryFactRequest fact = ObjectiveFactKeys.observationStory(
                        subject, story, LocalDateTime.now(ZONE_CN));
                if (fact == null) {
                    continue;
                }
                factService.recordObjective(userId, "backfill", List.of(fact));
                created++;
            } catch (Exception e) {
                log.debug("聚合叙事回填失败 subject={}（跳过）：{}", subject, e.toString());
            }
        }
        if (created > 0) {
            log.info("双层记忆回填：补了 {} 条策略聚合叙事", created);
        }
    }

    /** 取代链上的最新值 → 一句话（有验证说验证，没验证说回测，再没有说模拟盘）。 */
    static String storyFromValues(Map<String, String> values) {
        StringBuilder story = new StringBuilder();
        String verifyCells = values.get(ObjectiveFactKeys.VERIFY_VALID_CELLS_COUNT);
        if (verifyCells != null && !"0".equals(verifyCells.trim())) {
            story.append("样本外验证：").append(verifyCells.trim()).append(" 个有效样本");
            String beat = values.get(ObjectiveFactKeys.VERIFY_BEAT_BUY_AND_HOLD_COUNT);
            if (beat != null) {
                story.append("，其中 ").append(beat.trim()).append(" 个跑赢买入持有");
            }
            String avg = values.get(ObjectiveFactKeys.VERIFY_AVG_EXCESS_PCT);
            if (avg != null) {
                story.append("，平均超额 ").append(avg.trim()).append("%");
            }
            return story.toString();
        }
        String total = values.get(ObjectiveFactKeys.BACKTEST_TOTAL_RETURN_PCT);
        if (total != null) {
            story.append("回测结论：总收益 ").append(total.trim()).append("%");
            String bh = values.get(ObjectiveFactKeys.BACKTEST_BUY_AND_HOLD_RETURN_PCT);
            if (bh != null) {
                story.append("（同期买入持有 ").append(bh.trim()).append("%）");
            }
            String dd = values.get(ObjectiveFactKeys.BACKTEST_MAX_DRAWDOWN_PCT);
            if (dd != null) {
                story.append("，最大回撤 ").append(dd.trim()).append("%");
            }
            return story.toString();
        }
        String paper = values.get(ObjectiveFactKeys.PAPER_RETURN_PCT);
        if (paper != null) {
            return "模拟盘运行中，目前收益率 " + paper.trim() + "%";
        }
        return "";
    }
}
