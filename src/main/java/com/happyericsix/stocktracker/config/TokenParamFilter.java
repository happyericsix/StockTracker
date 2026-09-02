package com.happyericsix.stocktracker.config;

import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletRequestWrapper;
import jakarta.servlet.http.HttpServletResponse;
import org.springframework.web.filter.OncePerRequestFilter;

import java.io.IOException;
import java.util.Collections;
import java.util.Enumeration;

/**
 * EventSource 无法携带 Authorization 请求头，前端把 JWT 放在查询参数 token 中。
 * 本过滤器只拦截 SSE 流端点，把 ?token=xxx 包装成 Authorization: Bearer xxx，
 * 让后续 JWT 过滤器正常完成鉴权。
 */
public class TokenParamFilter extends OncePerRequestFilter {

    private static final String STREAM_PATH = "/api/v1/messages/stream";

    @Override
    protected void doFilterInternal(HttpServletRequest request, HttpServletResponse response, FilterChain chain)
            throws ServletException, IOException {
        String path = request.getRequestURI();
        String token = request.getParameter("token");

        if (path != null && path.endsWith(STREAM_PATH)
                && token != null && !token.isBlank()
                && request.getHeader("Authorization") == null) {

            HttpServletRequest wrapped = new HttpServletRequestWrapper(request) {
                @Override
                public String getHeader(String name) {
                    if ("Authorization".equalsIgnoreCase(name)) {
                        return "Bearer " + token;
                    }
                    return super.getHeader(name);
                }

                @Override
                public Enumeration<String> getHeaders(String name) {
                    if ("Authorization".equalsIgnoreCase(name)) {
                        return Collections.enumeration(Collections.singletonList("Bearer " + token));
                    }
                    return super.getHeaders(name);
                }
            };
            chain.doFilter(wrapped, response);
            return;
        }
        chain.doFilter(request, response);
    }
}
