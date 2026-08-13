package com.happyericsix.stocktracker.dto;

import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.Size;

public class RegisterRequest {
    @NotBlank @Size(min = 3, max = 50)
    private String username;
    private String email;
    private String phone;
    @NotBlank @Size(min = 6, max = 100)
    private String password;

    public RegisterRequest() {}
    public RegisterRequest(String username, String email, String phone, String password) {
        this.username = username; this.email = email; this.phone = phone; this.password = password;
    }
    public String getUsername() { return username; }
    public void setUsername(String username) { this.username = username; }
    public String getEmail() { return email; }
    public void setEmail(String email) { this.email = email; }
    public String getPhone() { return phone; }
    public void setPhone(String phone) { this.phone = phone; }
    public String getPassword() { return password; }
    public void setPassword(String password) { this.password = password; }
}
