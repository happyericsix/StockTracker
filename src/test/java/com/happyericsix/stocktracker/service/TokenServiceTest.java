package com.happyericsix.stocktracker.service;

import org.junit.jupiter.api.Test;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.oauth2.jwt.JwtClaimsSet;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.JwtEncoderParameters;

import java.time.Instant;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 30 天自动登录（2026-11-07）：rememberMe 分档签发。
 * 此前所有 token 一律 24h——用户勾选的"30 天自动登录"在系统里并不存在。
 */
class TokenServiceTest {

    /** 编码器测试替身：把 claims 透传出来（不真签名，测的是过期分档）。 */
    private static class CapturingEncoder implements JwtEncoder {
        JwtClaimsSet captured;

        @Override
        public org.springframework.security.oauth2.jwt.Jwt encode(JwtEncoderParameters parameters) {
            captured = parameters.getClaims();
            return org.springframework.security.oauth2.jwt.Jwt.withTokenValue("t")
                    .header("alg", "RS256")
                    .claims(c -> c.putAll(parameters.getClaims().getClaims()))
                    .build();
        }
    }

    private Authentication auth() {
        return new UsernamePasswordAuthenticationToken("alice", "pw", List.of());
    }

    @Test
    void rememberMeIssuesThirtyDayToken() {
        CapturingEncoder encoder = new CapturingEncoder();
        TokenService service = new TokenService(encoder);
        Instant before = Instant.now();

        service.generateToken(auth(), true);

        long days = java.time.temporal.ChronoUnit.SECONDS.between(before,
                encoder.captured.getExpiresAt());
        assertTrue(days >= 29 * 86400 && days <= 31 * 86400,
                "勾选 30 天自动登录应签 30 天 token，实际 " + days / 86400 + " 天");
    }

    @Test
    void defaultRemainsTwentyFourHours() {
        CapturingEncoder encoder = new CapturingEncoder();
        TokenService service = new TokenService(encoder);
        Instant before = Instant.now();

        service.generateToken(auth(), false);
        long days = java.time.temporal.ChronoUnit.SECONDS.between(before,
                encoder.captured.getExpiresAt());
        assertTrue(days >= 23 * 3600 && days <= 25 * 3600,
                "未勾选应保持 24 小时，实际 " + days / 3600 + " 小时");

        // 老签名（无双参）同样 24h：QR 扫码登录走的就是它
        service.generateToken(auth());
        long days2 = java.time.temporal.ChronoUnit.SECONDS.between(before,
                encoder.captured.getExpiresAt());
        assertTrue(days2 >= 23 * 3600 && days2 <= 25 * 3600);
    }
}
