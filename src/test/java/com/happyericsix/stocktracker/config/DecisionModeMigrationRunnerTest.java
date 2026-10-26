package com.happyericsix.stocktracker.config;

import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.service.ExecutionContract;
import com.happyericsix.stocktracker.util.CnTime;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyList;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 存量决策来源迁移（C11，2026-10-15）：null/rule 行统一为 agent，
 * 分界日打迁移当天 —— 否则这批策略永远停在规则路径（切换端点已封死，无任何入口）。
 */
@ExtendWith(MockitoExtension.class)
class DecisionModeMigrationRunnerTest {

    @Mock
    private StrategyRepository strategyRepository;

    @InjectMocks
    private DecisionModeMigrationRunner runner;

    private Strategy legacy(String mode) {
        return Strategy.builder()
                .id(7L).name("存量").symbol("600519")
                .configJson("{}").decisionMode(mode)
                .user(User.builder().id(1L).username("alice").password("x").email("a@b.c").build())
                .build();
    }

    /** null（建表以来的默认）与 rule（早期值）都迁到 agent，分界日 = 迁移当天。 */
    @Test
    @SuppressWarnings("unchecked")
    void migratesNullAndRuleStrategiesToAgentWithTodaysBoundary() {
        Strategy nullMode = legacy(null);
        Strategy ruleMode = legacy("rule");
        when(strategyRepository.findByDecisionModeIsNullOrDecisionModeNot(
                ExecutionContract.DECISION_MODE_AGENT)).thenReturn(List.of(nullMode, ruleMode));

        runner.run();

        ArgumentCaptor<List<Strategy>> captor = ArgumentCaptor.forClass(List.class);
        verify(strategyRepository).saveAll(captor.capture());
        for (Strategy migrated : captor.getValue()) {
            assertEquals(ExecutionContract.DECISION_MODE_AGENT, migrated.getDecisionMode());
            assertEquals(CnTime.today(), migrated.getDecisionModeSince(),
                    "分界线必须画在真实切换的那天（迁移当天），两侧曲线才不会被当成可比");
        }
    }

    /** 幂等：没有存量（或已迁完）时不动库 —— 第二次启动空转。 */
    @Test
    void noLegacyStrategiesMeansNoWrites() {
        when(strategyRepository.findByDecisionModeIsNullOrDecisionModeNot(
                ExecutionContract.DECISION_MODE_AGENT)).thenReturn(List.of());

        runner.run();

        verify(strategyRepository, never()).saveAll(anyList());
        verify(strategyRepository, never()).save(any(Strategy.class));
    }
}
