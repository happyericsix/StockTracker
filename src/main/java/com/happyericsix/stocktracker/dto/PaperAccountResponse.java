package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.PaperAccount;

import java.math.BigDecimal;
import java.time.LocalDateTime;

/**
 * 账户的读取视图。金额一律 {@link BigDecimal}：账里的精度不该在展示层被换成 double
 * （JSON 里仍然是数字，前端与调用方感知不到差别）。
 */
public class PaperAccountResponse {
    private Long id;
    private BigDecimal initialCapital;
    private BigDecimal cash;
    private BigDecimal shares;
    private BigDecimal avgCost;
    private BigDecimal equity;
    private BigDecimal highWatermark;
    private BigDecimal lastPrice;
    private String lastSignal;
    private LocalDateTime lastEvalAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    public PaperAccountResponse() {}

    public PaperAccountResponse(Long id, BigDecimal initialCapital, BigDecimal cash, BigDecimal shares,
                                BigDecimal avgCost, BigDecimal equity, BigDecimal highWatermark,
                                BigDecimal lastPrice, String lastSignal, LocalDateTime lastEvalAt,
                                LocalDateTime createdAt, LocalDateTime updatedAt) {
        this.id = id;
        this.initialCapital = initialCapital;
        this.cash = cash;
        this.shares = shares;
        this.avgCost = avgCost;
        this.equity = equity;
        this.highWatermark = highWatermark;
        this.lastPrice = lastPrice;
        this.lastSignal = lastSignal;
        this.lastEvalAt = lastEvalAt;
        this.createdAt = createdAt;
        this.updatedAt = updatedAt;
    }

    public static PaperAccountResponse from(PaperAccount entity) {
        return new PaperAccountResponse(
                entity.getId(),
                entity.getInitialCapital(),
                entity.getCash(),
                entity.getShares(),
                entity.getAvgCost(),
                entity.getEquity(),
                entity.getHighWatermark(),
                entity.getLastPrice(),
                entity.getLastSignal(),
                entity.getLastEvalAt(),
                entity.getCreatedAt(),
                entity.getUpdatedAt()
        );
    }

    public Long getId() { return id; }
    public void setId(Long id) { this.id = id; }
    public BigDecimal getInitialCapital() { return initialCapital; }
    public void setInitialCapital(BigDecimal initialCapital) { this.initialCapital = initialCapital; }
    public BigDecimal getCash() { return cash; }
    public void setCash(BigDecimal cash) { this.cash = cash; }
    public BigDecimal getShares() { return shares; }
    public void setShares(BigDecimal shares) { this.shares = shares; }
    public BigDecimal getAvgCost() { return avgCost; }
    public void setAvgCost(BigDecimal avgCost) { this.avgCost = avgCost; }
    public BigDecimal getEquity() { return equity; }
    public void setEquity(BigDecimal equity) { this.equity = equity; }
    public BigDecimal getHighWatermark() { return highWatermark; }
    public void setHighWatermark(BigDecimal highWatermark) { this.highWatermark = highWatermark; }
    public BigDecimal getLastPrice() { return lastPrice; }
    public void setLastPrice(BigDecimal lastPrice) { this.lastPrice = lastPrice; }
    public String getLastSignal() { return lastSignal; }
    public void setLastSignal(String lastSignal) { this.lastSignal = lastSignal; }
    public LocalDateTime getLastEvalAt() { return lastEvalAt; }
    public void setLastEvalAt(LocalDateTime lastEvalAt) { this.lastEvalAt = lastEvalAt; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(LocalDateTime updatedAt) { this.updatedAt = updatedAt; }
}
