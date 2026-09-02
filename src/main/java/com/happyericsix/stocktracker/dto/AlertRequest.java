package com.happyericsix.stocktracker.dto;

import jakarta.validation.constraints.AssertTrue;
import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.Max;
import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Pattern;

public class AlertRequest {

    private static final String TYPE_REGEX =
            "^(price_above|price_below|pnl_percent|rsi_overbought|rsi_oversold" +
            "|macd_golden_cross|macd_death_cross|trailing_take_profit)$";

    @NotBlank(message = "股票代码不能为空")
    @Pattern(regexp = "^[A-Z0-9.-]{1,10}$", message = "股票代码格式不正确（仅允许字母数字.-，最长 10 位）")
    private String symbol;

    @NotBlank(message = "预警类型不能为空")
    @Pattern(regexp = TYPE_REGEX, message = "不支持的预警类型")
    private String conditionType;

    @NotNull(message = "阈值不能为空")
    private Double threshold;

    private Boolean enabled;

    // ===== v1 边沿触发参数(可选) =====
    @Min(value = 0, message = "冷却分钟数不能为负数")
    @Max(value = 1440, message = "冷却分钟数不能超过 1440（24 小时）")
    private Integer cooldownMinutes;

    @DecimalMin(value = "0.0", inclusive = false, message = "重置比率必须 > 0")
    @DecimalMax(value = "1.0", message = "重置比率不能超过 1.0")
    private Double resetRatio;

    @Min(value = 1, message = "重置小时数必须 ≥ 1")
    @Max(value = 720, message = "重置小时数不能超过 720（30 天）")
    private Integer reArmHours;

    public AlertRequest() {}

    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public String getConditionType() { return conditionType; }
    public void setConditionType(String conditionType) { this.conditionType = conditionType; }
    public Double getThreshold() { return threshold; }
    public void setThreshold(Double threshold) { this.threshold = threshold; }
    public Boolean getEnabled() { return enabled; }
    public void setEnabled(Boolean enabled) { this.enabled = enabled; }
    public Integer getCooldownMinutes() { return cooldownMinutes; }
    public void setCooldownMinutes(Integer cooldownMinutes) { this.cooldownMinutes = cooldownMinutes; }
    public Double getResetRatio() { return resetRatio; }
    public void setResetRatio(Double resetRatio) { this.resetRatio = resetRatio; }
    public Integer getReArmHours() { return reArmHours; }
    public void setReArmHours(Integer reArmHours) { this.reArmHours = reArmHours; }

    // ===== 跨字段校验:threshold 范围取决于 conditionType =====
    @AssertTrue(message = "threshold 范围不合法(price 类型需 > 0,RSI/回撤类型需在 (0,100) 之间)")
    public boolean isThresholdRangeValid() {
        if (threshold == null) {
            return true;  // 留给 @NotNull 处理
        }
        return switch (conditionType == null ? "" : conditionType) {
            case "price_above", "price_below" -> threshold > 0;
            case "rsi_overbought", "rsi_oversold", "trailing_take_profit"
                    -> threshold > 0 && threshold < 100;
            case "pnl_percent" -> true;  // 正数止盈、负数止损都允许
            case "macd_golden_cross", "macd_death_cross" -> true;  // 这两个用户不该传,但宽容处理
            default -> false;
        };
    }
}
