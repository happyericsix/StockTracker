package com.happyericsix.stocktracker.dto;

public class UnreadCountResponse {
    private long count;

    public UnreadCountResponse() {}
    public UnreadCountResponse(long count) { this.count = count; }

    public long getCount() { return count; }
    public void setCount(long count) { this.count = count; }
}
