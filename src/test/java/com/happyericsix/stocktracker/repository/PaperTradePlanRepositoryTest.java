package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperTradePlan;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.Mockito.when;

/**
 * 生效预案的重叠日语义（2026-11-18 线上事故的回归钉）。
 *
 * <p>委员会每天签发 valid_days 最多 2 天的预案，交界日（昨日预案的尾日 + 今日预案的
 * 首日）必然两行同时命中 plan_date ≤ today ≤ valid_until。旧实现用 Optional 单结果
 * 查询直收，这一天每个盘中评估都抛 IncorrectResultSize（线上实测一天 246 次），
 * 预案的盘中确定性执行臂全天瘫痪。
 *
 * <p>修复后的契约：底层查询<b>必须是 List</b>（多行是常态），默认方法取首行
 * （最新签发）。这里用 mock 钉住这层语义——真正的爆炸根因（单结果查询遇两行）
 * 已随查询形状的改变在结构上消除，mock 测试守住的是"未来重构不许退回单结果"。
 */
class PaperTradePlanRepositoryTest {

    private static final Long STRATEGY_ID = 4L;
    private static final LocalDate OVERLAP_DAY = LocalDate.of(2026, 11, 18);

    /** 线上 11-18 的真实形状：尾日旧预案 + 首日新预案同时命中，取最新、不抛异常。 */
    @Test
    void overlappingValidityDaysReturnLatestPlan() {
        PaperTradePlanRepository repository = Mockito.mock(PaperTradePlanRepository.class,
                Mockito.CALLS_REAL_METHODS);
        PaperTradePlan oldPlan = plan("旧预案(尾日)");
        PaperTradePlan newPlan = plan("新预案(首日)");
        // ORDER BY plan_date desc, id desc 的查询形状：最新在前
        when(repository.findActiveCandidatesForStrategy(STRATEGY_ID, OVERLAP_DAY))
                .thenReturn(List.of(newPlan, oldPlan));

        Optional<PaperTradePlan> active = repository.findActiveForStrategy(STRATEGY_ID, OVERLAP_DAY);

        assertEquals(newPlan, active.orElseThrow(), "交界日取最新签发的那份，旧预案自然失语");
    }

    /** 无候选（过期/未签发）= 空 Optional，调用方的"无预案"分支照常工作。 */
    @Test
    void emptyWhenNoActiveCandidates() {
        PaperTradePlanRepository repository = Mockito.mock(PaperTradePlanRepository.class,
                Mockito.CALLS_REAL_METHODS);
        when(repository.findActiveCandidatesForStrategy(Mockito.anyLong(), Mockito.any()))
                .thenReturn(List.of());

        assertTrue(repository.findActiveForStrategy(STRATEGY_ID, OVERLAP_DAY).isEmpty());
    }

    private static PaperTradePlan plan(String marker) {
        return PaperTradePlan.builder().planJson(marker).build();
    }
}
