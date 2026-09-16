package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.PaperAccount;

import java.time.LocalDateTime;

public class PaperAccountResponse {
    private Long id;
    private Double initialCapital;
    private Double cash;
    private Double shares;
    private Double avgCost;
    private Double equity;
    private Double highWatermark;
    private Double lastPrice;
    private String lastSignal;
    private LocalDateTime lastEvalAt;
    private LocalDateTime createdAt;
    private LocalDateTime updatedAt;

    public PaperAccountResponse() {}

    public PaperAccountResponse(Long id, Double initialCapital, Double cash, Double shares,
                                Double avgCost, Double equity, Double highWatermark,
                                Double lastPrice, String lastSignal, LocalDateTime lastEvalAt,
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
    public Double getInitialCapital() { return initialCapital; }
    public void setInitialCapital(Double initialCapital) { this.initialCapital = initialCapital; }
    public Double getCash() { return cash; }
    public void setCash(Double cash) { this.cash = cash; }
    public Double getShares() { return shares; }
    public void setShares(Double shares) { this.shares = shares; }
    public Double getAvgCost() { return avgCost; }
    public void setAvgCost(Double avgCost) { this.avgCost = avgCost; }
    public Double getEquity() { return equity; }
    public void setEquity(Double equity) { this.equity = equity; }
    public Double getHighWatermark() { return highWatermark; }
    public void setHighWatermark(Double highWatermark) { this.highWatermark = highWatermark; }
    public Double getLastPrice() { return lastPrice; }
    public void setLastPrice(Double lastPrice) { this.lastPrice = lastPrice; }
    public String getLastSignal() { return lastSignal; }
    public void setLastSignal(String lastSignal) { this.lastSignal = lastSignal; }
    public LocalDateTime getLastEvalAt() { return lastEvalAt; }
    public void setLastEvalAt(LocalDateTime lastEvalAt) { this.lastEvalAt = lastEvalAt; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
    public LocalDateTime getUpdatedAt() { return updatedAt; }
    public void setUpdatedAt(LocalDateTime updatedAt) { this.updatedAt = updatedAt; }
}
