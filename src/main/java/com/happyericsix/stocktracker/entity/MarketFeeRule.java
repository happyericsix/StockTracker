package com.happyericsix.stocktracker.entity;

import jakarta.persistence.*;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.math.BigDecimal;
import java.time.LocalDate;

/**
 * 官方费率表：**按生效日期查当天适用的费率**，而不是把"今天的费率"写成常量。
 *
 * <p>费率是会变的（印花税 2023-08-28 减半、过户费 2022-04-29 下调 50%），
 * 变化的形式是公告 + 生效日。查表键是 {@code ruleKey + effectiveFrom ≤ 结算日}，
 * 于是同一套结算代码跑历史回放时自动用当时真实的费率；
 * 新公告出来只需要加一行数据（{@link com.happyericsix.stocktracker.service.MarketRulesService}
 * 的种子表同步补一行），不需要改任何结算代码。
 */
@Entity
@Table(name = "market_fee_rules",
        uniqueConstraints = @UniqueConstraint(columnNames = {"rule_key", "effective_from"}))
@Data
@Builder
@NoArgsConstructor
@AllArgsConstructor
public class MarketFeeRule {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 规则键：STAMP_TAX_SELL（卖出印花税）/ TRANSFER_FEE（过户费，双边）。 */
    @Column(name = "rule_key", nullable = false, length = 32)
    private String ruleKey;

    /** 生效日（含）。结算日 ≥ 生效日的最新一条即适用。 */
    @Column(name = "effective_from", nullable = false)
    private LocalDate effectiveFrom;

    /** 费率，百分数（0.05 = 万分之五）。与策略配置 risk.*_pct 同一单位。 */
    @Column(name = "rate_pct", nullable = false, precision = 8, scale = 5)
    private BigDecimal ratePct;

    /** 公告依据（人能核对的出处，如"财政部 税务总局公告 2023 年第 39 号"）。 */
    @Column(name = "source_note", length = 200)
    private String sourceNote;
}
