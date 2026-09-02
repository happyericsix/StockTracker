package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.entity.PaperTrade;

import java.time.LocalDate;
import java.time.LocalDateTime;

public class PaperTradeResponse {
    private LocalDate tradeDate;
    private String symbol;
    private String side;
    private Double price;
    private Double shares;
    private Double amount;
    private String reason;
    private LocalDateTime createdAt;

    public PaperTradeResponse() {}

    public PaperTradeResponse(LocalDate tradeDate, String symbol, String side,
                              Double price, Double shares, Double amount,
                              String reason, LocalDateTime createdAt) {
        this.tradeDate = tradeDate;
        this.symbol = symbol;
        this.side = side;
        this.price = price;
        this.shares = shares;
        this.amount = amount;
        this.reason = reason;
        this.createdAt = createdAt;
    }

    public static PaperTradeResponse from(PaperTrade entity) {
        return new PaperTradeResponse(
                entity.getTradeDate(),
                entity.getSymbol(),
                entity.getSide(),
                entity.getPrice(),
                entity.getShares(),
                entity.getAmount(),
                entity.getReason(),
                entity.getCreatedAt()
        );
    }

    public LocalDate getTradeDate() { return tradeDate; }
    public void setTradeDate(LocalDate tradeDate) { this.tradeDate = tradeDate; }
    public String getSymbol() { return symbol; }
    public void setSymbol(String symbol) { this.symbol = symbol; }
    public String getSide() { return side; }
    public void setSide(String side) { this.side = side; }
    public Double getPrice() { return price; }
    public void setPrice(Double price) { this.price = price; }
    public Double getShares() { return shares; }
    public void setShares(Double shares) { this.shares = shares; }
    public Double getAmount() { return amount; }
    public void setAmount(Double amount) { this.amount = amount; }
    public String getReason() { return reason; }
    public void setReason(String reason) { this.reason = reason; }
    public LocalDateTime getCreatedAt() { return createdAt; }
    public void setCreatedAt(LocalDateTime createdAt) { this.createdAt = createdAt; }
}
