package com.happyericsix.stocktracker.config;

import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.service.ExecutionContract;
import com.happyericsix.stocktracker.util.CnTime;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.CommandLineRunner;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 存量策略的决策来源统一迁移（2026-10-15 承诺的"数据迁移"，此前只有注释没有实现）。
 *
 * <h3>背景</h3>
 * 2026-10-15 起产品面不再有"纯 rule 模式"：新策略一律由 agent 委员会决策
 * （createStrategy/createFromAgent 都显式写 agent），切换接口也已封死。
 * 但存量行是 rule 或 null —— 读侧按 rule 兼容（ExecutionContract.normalizeDecisionMode），
 * 于是这批策略**永远**停留在规则路径：没有首会、没有授权、没有 experience 回流，
 * 且没有任何入口能把它们变成 agent（切换端点无条件抛异常）。
 *
 * <h3>迁移语义</h3>
 * <ul>
 *   <li>decisionMode 置为 agent；decisionModeSince 打**迁移当天** —— 净值图的
 *       "切换决策来源"分界线要画在真实发生切换的那一天，曲线两侧才不会被当成可比；</li>
 *   <li>幂等：只更新还不是 agent 的行，第二次启动空转；</li>
 *   <li>启动时一次性完成，先于任何定时结算/用户操作 —— 迁移后的第一次日线结算
 *       会触发委员会的 first_decision 召集门（每条约 2 万 token），这是"统一为
 *       agent"的本意，不是事故。</li>
 * </ul>
 */
@Component
public class DecisionModeMigrationRunner implements CommandLineRunner {

    private static final Logger log = LoggerFactory.getLogger(DecisionModeMigrationRunner.class);

    private final StrategyRepository strategyRepository;

    public DecisionModeMigrationRunner(StrategyRepository strategyRepository) {
        this.strategyRepository = strategyRepository;
    }

    @Override
    public void run(String... args) {
        List<Strategy> legacy = strategyRepository
                .findByDecisionModeIsNullOrDecisionModeNot(ExecutionContract.DECISION_MODE_AGENT);
        if (legacy.isEmpty()) {
            return;
        }
        var today = CnTime.today();
        for (Strategy strategy : legacy) {
            strategy.setDecisionMode(ExecutionContract.DECISION_MODE_AGENT);
            strategy.setDecisionModeSince(today);
        }
        strategyRepository.saveAll(legacy);
        log.info("Migrated {} legacy strategies to agent decision mode (since={}) — "
                + "their next daily settlement will convene the committee (first_decision)",
                legacy.size(), today);
    }
}
