package com.happyericsix.stocktracker.config;

import com.nimbusds.jose.jwk.JWK;
import com.nimbusds.jose.jwk.JWKSet;
import com.nimbusds.jose.jwk.RSAKey;
import com.nimbusds.jose.jwk.source.ImmutableJWKSet;
import com.nimbusds.jose.jwk.source.JWKSource;
import com.nimbusds.jose.proc.SecurityContext;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.security.authentication.AnonymousAuthenticationToken;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.config.Customizer;
import org.springframework.security.config.annotation.authentication.configuration.AuthenticationConfiguration;
import org.springframework.security.config.annotation.method.configuration.EnableMethodSecurity;
import org.springframework.security.config.annotation.web.builders.HttpSecurity;
import org.springframework.security.config.annotation.web.configuration.EnableWebSecurity;
import org.springframework.security.config.http.SessionCreationPolicy;
import org.springframework.security.core.AuthenticationException;
import org.springframework.security.core.authority.AuthorityUtils;
import org.springframework.security.core.context.SecurityContextHolder;
import org.springframework.security.crypto.bcrypt.BCryptPasswordEncoder;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.security.oauth2.jwt.JwtDecoder;
import org.springframework.security.oauth2.jwt.JwtEncoder;
import org.springframework.security.oauth2.jwt.NimbusJwtDecoder;
import org.springframework.security.oauth2.jwt.NimbusJwtEncoder;
import org.springframework.security.oauth2.server.resource.web.authentication.BearerTokenAuthenticationFilter;
import org.springframework.security.web.AuthenticationEntryPoint;
import org.springframework.security.web.SecurityFilterChain;
import org.springframework.web.cors.CorsConfiguration;
import org.springframework.web.cors.CorsConfigurationSource;
import org.springframework.web.cors.UrlBasedCorsConfigurationSource;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;
import java.io.IOException;
import java.util.List;

@Configuration
@EnableWebSecurity
@EnableMethodSecurity
public class SecurityConfig {

    @Bean
    public SecurityFilterChain filterChain(HttpSecurity http) throws Exception {
        http
                .csrf(csrf -> csrf.disable())
                .cors(cors -> cors.configurationSource(corsConfigurationSource()))
                .sessionManagement(session ->
                        session.sessionCreationPolicy(SessionCreationPolicy.STATELESS))
                .authorizeHttpRequests(auth -> auth
                        .requestMatchers("/api/v1/auth/login", "/api/v1/auth/register").permitAll()
                        // 扫码发生在登录之前，所以这两个必须放行（设计文档 §6.4）。
                        // 注意：只放行 qr/**，/sync /status /bind 仍需 JWT。
                        // ⚠️ 改动这里时，务必同步改下面 InvalidTokenToleratedEntryPoint 的 PUBLIC_PATHS，
                        //    否则可能出现"permitAll 了但带无效 token 还是被拦"的不一致。
                        .requestMatchers("/api/v1/ths/qr/**").permitAll()
                        // 记忆系统内部接口：调用方是 Python agent（不带 JWT，只带共享密钥）。
                        // permitAll 在这里是必须的，真正的门是控制器里的 X-Internal-Token 校验
                        // （见 InternalMemoryController#authorized，未配置密钥时一律拒绝）。
                        // ⚠️ 这些接口能读到用户全部对话记忆，新增路径前先想清楚。
                        .requestMatchers("/api/v1/internal/**").permitAll()
                        .requestMatchers("/api/v1/stocks/**").authenticated()
                        .anyRequest().authenticated()
                )
                .addFilterBefore(new TokenParamFilter(), BearerTokenAuthenticationFilter.class)
                .oauth2ResourceServer(oauth2 -> oauth2
                        .jwt(Customizer.withDefaults())
                        // ⚠️ 关键：前端 request.js 会无条件带上 localStorage 里的 token，
                        //    包括"已经过期/失效"的情况。OAuth2 资源服务器只要看到
                        //    Authorization 头就会尝试验证，失败直接 401 ——
                        //    连 permitAll() 的接口也会被拦，导致登录页扫码直接失效
                        //    （实测：带无效 JWT 调 POST /ths/qr/create 返回 401）。
                        //
                        //    这里的处理：无效 token 只当作"未认证"，不直接拒绝。
                        //    · 扫码等 permitAll 接口 → 正常放行（按未登录处理，走"扫码登录"分支）
                        //    · 需要认证的接口 → 由 authorizeHttpRequests 拦下并返回 401/403
                        .authenticationEntryPoint(new InvalidTokenToleratedEntryPoint()));
        return http.build();
    }

    @Bean
    public CorsConfigurationSource corsConfigurationSource() {
        CorsConfiguration config = new CorsConfiguration();
        config.setAllowedOrigins(List.of("*"));
        config.setAllowedMethods(List.of("GET", "POST", "PUT", "DELETE", "OPTIONS"));
        config.setAllowedHeaders(List.of("*"));
        UrlBasedCorsConfigurationSource source = new UrlBasedCorsConfigurationSource();
        source.registerCorsConfiguration("/**", config);
        return source;
    }

    @Bean
    public JwtDecoder jwtDecoder(RsaKeyProperties rsaKeys) {
        return NimbusJwtDecoder.withPublicKey(rsaKeys.publicKey()).build();
    }

    @Bean
    public JwtEncoder jwtEncoder(RsaKeyProperties rsaKeys) {
        JWK jwk = new RSAKey.Builder(rsaKeys.publicKey())
                .privateKey(rsaKeys.privateKey()).build();
        JWKSource<SecurityContext> jwks = new ImmutableJWKSet<>(new JWKSet(jwk));
        return new NimbusJwtEncoder(jwks);
    }

    @Bean
    public PasswordEncoder passwordEncoder() { return new BCryptPasswordEncoder(); }

    @Bean
    public AuthenticationManager authenticationManager(AuthenticationConfiguration config) throws Exception {
        return config.getAuthenticationManager();
    }

    /**
     * 「仅在公开路径上容忍无效 token」的认证入口点。
     *
     * <p>背景：前端 {@code request.js} 会无条件把 localStorage 里的 token 放进
     * {@code Authorization} 头，包括已经过期/失效的。Spring 的 OAuth2 资源服务器
     * 只要看到这个头就会尝试验证，验证失败就走 authenticationEntryPoint ——
     * <b>默认实现是直接 401</b>，于是连 {@code permitAll()} 的扫码接口也被拦掉，
     * 登录页扫码直接失效（实测确认）。
     *
     * <p>⚠️ <b>这里必须按路径区分</b>，不能全局容忍：
     * {@code AnonymousAuthenticationToken.isAuthenticated()} 返回 <b>true</b>，
     * 所以如果对所有路径都塞匿名身份，{@code anyRequest().authenticated()}
     * 就会被满足，保护端点（/sync、/status、/bind）会<b>无凭证直接放行</b>
     * —— 这是一个严重的安全漏洞（开发期实测到过：无 JWT 调 /ths/status 返回 200）。
     *
     * <p>正确做法：
     * <ul>
     *   <li>请求落在 {@link #PUBLIC_PATHS} 里 → 塞匿名身份放行，让请求继续
     *       到授权层（这些路径本来就是 permitAll）</li>
     *   <li>其它路径 → <b>照常返回 401</b>，绝不放行</li>
     * </ul>
     */
    private static class InvalidTokenToleratedEntryPoint implements AuthenticationEntryPoint {
        @Override
        public void commence(HttpServletRequest request,
                             HttpServletResponse response,
                             AuthenticationException authException) throws IOException {
            if (isPublicPath(request.getRequestURI())) {
                // 公开路径：放匿名身份，让请求继续往下走
                org.springframework.security.core.context.SecurityContext context =
                        SecurityContextHolder.createEmptyContext();
                context.setAuthentication(new AnonymousAuthenticationToken(
                        "anonymousKey", "anonymousUser",
                        AuthorityUtils.createAuthorityList("ROLE_ANONYMOUS")));
                SecurityContextHolder.setContext(context);
                return;
            }

            // 保护路径：维持 401，不做任何容忍
            response.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            response.setContentType("application/json;charset=UTF-8");
            response.getWriter().write("{\"code\":401,\"message\":\"登录已失效，请重新登录\"}");
        }
    }

    /**
     * 允许「带无效 token 也放行」的公开路径。
     *
     * <p>必须和 {@code authorizeHttpRequests} 里 permitAll 的路径保持一致 ——
     * 这里多写一个路径就等于把一个接口暴露出去，改动时两处要一起看。
     */
    private static final List<String> PUBLIC_PATHS = List.of(
            "/api/v1/auth/login",
            "/api/v1/auth/register",
            "/api/v1/ths/qr/create",
            "/api/v1/ths/qr/poll",
            // 记忆内部接口：同样需要在"带无效 token"时放行，否则前端误带过期 JWT 反而会 401
            "/api/v1/internal"
    );

    private static boolean isPublicPath(String uri) {
        if (uri == null) {
            return false;
        }
        for (String p : PUBLIC_PATHS) {
            // 用 startsWith 是为了容忍尾部的查询串/子路径
            if (uri.equals(p) || uri.startsWith(p + "/")) {
                return true;
            }
        }
        return false;
    }
}