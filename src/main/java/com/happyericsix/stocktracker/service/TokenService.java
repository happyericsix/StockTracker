package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.config.RsaKeyProperties;
import org.springframework.security.core.Authentication;
import org.springframework.security.core.GrantedAuthority;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;
import org.springframework.stereotype.Service;

import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.stream.Collectors;

@Service
public class TokenService {

    private final JwtEncoder encoder;

    public TokenService(JwtEncoder encoder) {
        this.encoder = encoder;
    }

    public String generateToken(Authentication authentication) {
        return generateToken(authentication, false);
    }

    /**
     * @param rememberMe 30 天自动登录（2026-11-07）：true = 30 天，false/null = 24 小时。
     *                   长短期分档而不是一刀切 30 天：丢失 30 天 token 的代价
     *                   （别人拿到能操作一个月）不该强加给没勾选的用户。
     */
    public String generateToken(Authentication authentication, boolean rememberMe) {
        Instant now = Instant.now();
        String scope = authentication.getAuthorities().stream()
                .map(GrantedAuthority::getAuthority)
                .collect(Collectors.joining(" "));

        JwtClaimsSet claims = JwtClaimsSet.builder()
                .issuer("stock-tracker")
                .issuedAt(now)
                .expiresAt(now.plus(rememberMe ? 30 : 1, ChronoUnit.DAYS))
                .subject(authentication.getName())
                .claim("scope", scope)
                .build();

        return encoder.encode(JwtEncoderParameters.from(claims)).getTokenValue();
    }
}