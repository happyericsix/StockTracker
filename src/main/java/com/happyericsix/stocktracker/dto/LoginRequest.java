package com.happyericsix.stocktracker.dto;

import jakarta.validation.constraints.NotBlank;

public class LoginRequest {
    @NotBlank
    private String username;
    @NotBlank
    private String password;
    /**
     * 30 天自动登录（2026-11-07）：勾选后 token 有效期 30 天，否则 24 小时。
     * 此前系统里根本不存在记住登录——所有 token 一律 24h，用户以为的
     * "每次更新都要重新登录"其实是每天过期一次与每日部署时间重合的错觉
     * （签名密钥是卷挂载的，部署并不作废 token）。
     */
    private Boolean rememberMe;

    public LoginRequest() {}
    public LoginRequest(String username, String password) {
        this.username = username; this.password = password;
    }
    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getPassword() { return password; }
    public void setPassword(String password) { this.password = password; }
    public Boolean getRememberMe() { return rememberMe; }
    public void setRememberMe(Boolean rememberMe) { this.rememberMe = rememberMe; }
}
