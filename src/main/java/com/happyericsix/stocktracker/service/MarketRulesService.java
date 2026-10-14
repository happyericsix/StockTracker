package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.MarketFeeRule;
import com.happyericsix.stocktracker.repository.MarketFeeRuleRepository;
import jakarta.annotation.PostConstruct;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.util.List;
import java.util.Locale;

/**
 * A 股市场规则的**唯一出处**：官方费率按生效日期查表，板块规则（涨跌幅/整手/最小价位）
 * 按代码前缀 + ST 状态推导。结算与回测都不许再自带一套默认费率。
 *
 * <h3>种子数据 = 公告史</h3>
 * 每一行都是一条真实公告的落地。新公告 → 在 SEED 里加一行（effectiveFrom = 公告生效日），
 * 结算代码零改动。历史回放自动落在正确的费率区间上。
 *
 * <h3>查不到怎么办</h3>
 * 结算日早于种子表最早一条（理论上只有造数据才会遇到）时**退回表里最早一条**并告警：
 * 静默用 0 费率会把系统性缺口记成策略收益，宁可让日志说话。
 */
@Service
public class MarketRulesService {

    private static final Logger log = LoggerFactory.getLogger(MarketRulesService.class);

    public static final String RULE_STAMP_TAX_SELL = "STAMP_TAX_SELL";
    public static final String RULE_TRANSFER_FEE = "TRANSFER_FEE";

    /**
     * 公告史（按生效日升序）。写入依据：
     * <ul>
     *   <li>印花税：1990 年开征以来多次调整；2008-09-19 起改为**单边卖出 0.1%**
     *       （财政部、国家税务总局《关于调整证券（股票）交易印花税征收方式的通知》）；
     *       2023-08-28 起减半为**卖出 0.05%**（财政部 税务总局公告 2023 年第 39 号）。</li>
     *   <li>过户费（中国结算，双边）：2022-04-29 起总体下调 50% 至成交金额的
     *       **0.001%**（中国结算《关于降低股票类业务最低结算备付金缴纳比例与股票
     *       交易过户费收费标准的通知》，2022-04 发布）。</li>
     * </ul>
     */
    private static final List<MarketFeeRule> SEED = List.of(
            MarketFeeRule.builder().ruleKey(RULE_STAMP_TAX_SELL)
                    .effectiveFrom(LocalDate.of(2008, 9, 19))
                    .ratePct(new BigDecimal("0.1"))
                    .sourceNote("2008-09-19 起印花税改为单边卖出 0.1%（财政部/税务总局）")
                    .build(),
            MarketFeeRule.builder().ruleKey(RULE_STAMP_TAX_SELL)
                    .effectiveFrom(LocalDate.of(2023, 8, 28))
                    .ratePct(new BigDecimal("0.05"))
                    .sourceNote("财政部 税务总局公告 2023 年第 39 号：证券交易印花税减半征收")
                    .build(),
            MarketFeeRule.builder().ruleKey(RULE_TRANSFER_FEE)
                    .effectiveFrom(LocalDate.of(2015, 1, 1))
                    .ratePct(new BigDecimal("0.002"))
                    .sourceNote("过户费双边 0.002%（中国结算旧标准）")
                    .build(),
            MarketFeeRule.builder().ruleKey(RULE_TRANSFER_FEE)
                    .effectiveFrom(LocalDate.of(2022, 4, 29))
                    .ratePct(new BigDecimal("0.001"))
                    .sourceNote("中国结算 2022-04：股票交易过户费总体下调 50% 至 0.001%（双边）")
                    .build());

    private final MarketFeeRuleRepository repository;

    public MarketRulesService(MarketFeeRuleRepository repository) {
        this.repository = repository;
    }

    /** 幂等播种：库里有同键同生效日就跳过，新公告加种子后重启即入库。 */
    @PostConstruct
    @Transactional
    public void seed() {
        for (MarketFeeRule rule : SEED) {
            boolean exists = repository.findByRuleKeyOrderByEffectiveFromAsc(rule.getRuleKey())
                    .stream().anyMatch(r -> r.getEffectiveFrom().equals(rule.getEffectiveFrom()));
            if (!exists) {
                repository.save(rule);
                log.info("Seeded market fee rule {} effective {} -> {}%",
                        rule.getRuleKey(), rule.getEffectiveFrom(), rule.getRatePct());
            }
        }
    }

    /** 结算日适用的费率（百分数 → 小数由调用方按同一口径换算）。 */
    public BigDecimal ratePct(String ruleKey, LocalDate onDate) {
        LocalDate date = onDate == null ? LocalDate.now() : onDate;
        return repository
                .findFirstByRuleKeyAndEffectiveFromLessThanEqualOrderByEffectiveFromDesc(ruleKey, date)
                .or(() -> {
                    log.warn("No {} rule effective on or before {} — falling back to earliest seed; "
                            + "historical replay before the seed range is being approximated", ruleKey, date);
                    return repository
                            .findByRuleKeyOrderByEffectiveFromAsc(ruleKey)
                            .stream().findFirst();
                })
                .map(MarketFeeRule::getRatePct)
                .orElse(BigDecimal.ZERO);
    }

    // ==================== 板块规则（推导，不落库） ====================

    /**
     * 涨跌幅限制（%）：主板 10 / 创业板·科创板 20 / 北交所 30；主板 ST 5。
     * 名称带 ST 只影响主板——创业/科创/北交的 ST 仍是 20/30。
     */
    public static double limitPctFor(String symbol, String stockName) {
        String s = symbol == null ? "" : symbol.toUpperCase(Locale.ROOT);
        String n = stockName == null ? "" : stockName.toUpperCase(Locale.ROOT);
        boolean st = n.contains("ST"); // ST / *ST 都含 "ST"，即风险警示板
        if (s.startsWith(("688")) || s.startsWith("689")
                || s.startsWith("300") || s.startsWith("301") || s.startsWith("302")) {
            return 20.0;
        }
        if (s.startsWith("43") || s.startsWith("83") || s.startsWith("87")
                || s.startsWith("88") || s.startsWith("92")) {
            return 30.0;
        }
        return st ? 5.0 : 10.0;
    }

    /** 整手（股）：科创板 200 起购，北交所与主板 100。 */
    public static double lotFor(String symbol) {
        String s = symbol == null ? "" : symbol.toUpperCase(Locale.ROOT);
        return (s.startsWith("688") || s.startsWith("689")) ? 200.0 : 100.0;
    }

    /** 最小变动价位：A 股股票 0.01 元。 */
    public static final BigDecimal TICK = new BigDecimal("0.01");
}
