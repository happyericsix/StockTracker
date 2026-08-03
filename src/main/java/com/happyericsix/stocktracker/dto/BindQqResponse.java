package com.happyericsix.stocktracker.dto;

/**
 * 绑定状态查询/解绑响应
 */
public class BindQqResponse {
    private String qqNumber;
    private String username;
    private boolean bound;

    public BindQqResponse() {}
    public BindQqResponse(String qqNumber, String username, boolean bound) {
        this.qqNumber = qqNumber;
        this.username = username;
        this.bound = bound;
    }

    public static BindQqResponse unbound() {
        return new BindQqResponse(null, null, false);
    }

    public String getQqNumber() { return qqNumber; }
    public void setQqNumber(String qqNumber) { this.qqNumber = qqNumber; }
    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public boolean isBound() { return bound; }
    public void setBound(boolean bound) { this.bound = bound; }
}
