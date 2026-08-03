package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.BindQqResponse;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.exception.BusinessException;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.time.Duration;

/**
 * 用户相关业务：QQ 绑定/解绑/查询
 *
 * 绑定流程：
 * 1. 前端登录后调用 generateBindCode(username) → 后端生成 6 位验证码，存 Redis
 *    key=bind:code:{username}, value=code, ttl=5min，返回 code
 * 2. 用户在 QQ 给机器人发 "绑定 123456"
 * 3. Python webhook 调 verifyAndBind(qqId, code) → 后端从 Redis 取验证码校验
 *    校验通过：找到对应 username（通过 code 反查），写 user.qqNumber
 *
 * 注意：实际存储用 username 作为 key（多用户隔离），绑定时需要反查 code -> username
 * 解决方案：Redis value 存 "username|code" 或者用 hash 结构；这里用 hash 存 {code, username, createdAt}
 */
@Service
public class UserService {

    private static final Logger log = LoggerFactory.getLogger(UserService.class);
    private static final String CODE_PREFIX = "bind:code:";
    private static final Duration CODE_TTL = Duration.ofMinutes(5);
    private static final SecureRandom RANDOM = new SecureRandom();

    private final UserRepository userRepository;
    private final StringRedisTemplate redisTemplate;

    public UserService(UserRepository userRepository, StringRedisTemplate redisTemplate) {
        this.userRepository = userRepository;
        this.redisTemplate = redisTemplate;
    }

    /**
     * 为当前登录用户生成 6 位 QQ 绑定验证码
     * 同一用户重复调用会覆盖旧验证码
     */
    public String generateBindCode(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));

        // 如果已经绑定，直接返回
        if (user.getQqNumber() != null && !user.getQqNumber().isBlank()) {
            throw new BusinessException(400, "已绑定 QQ：" + user.getQqNumber() + "，请先解绑");
        }

        // 生成 6 位数字验证码
        String code = String.format("%06d", RANDOM.nextInt(1_000_000));
        String key = CODE_PREFIX + username;
        // value 存 username，验证时用 qqId 找最近一次对应的 username 比较复杂
        // 简单做法：value = "code"；绑定时按 qqId 找 user，但同一时间同一 qqId 只能被一个用户绑定
        redisTemplate.opsForValue().set(key, code, CODE_TTL);
        log.info("用户 {} 生成 QQ 绑定验证码 {} (ttl=5min)", username, code);
        return code;
    }

    /**
     * Python webhook 调用：QQ 用户在 QQ 发了 "绑定 123456"
     * 逻辑：扫所有有效的 bind:code:*，匹配 code 的，绑定到该用户
     */
    @Transactional
    public BindQqResponse verifyAndBind(String qqId, String code) {
        if (qqId == null || qqId.isBlank() || code == null || code.isBlank()) {
            throw new BusinessException(400, "qqId 和 code 不能为空");
        }

        // 校验 QQ 号格式（5-12 位数字）
        if (!qqId.matches("\\d{5,12}")) {
            throw new BusinessException(400, "QQ 号格式错误（应为5-12位数字）");
        }

        // 校验验证码格式（6 位数字）
        if (!code.matches("\\d{6}")) {
            throw new BusinessException(400, "验证码格式错误（应为6位数字）");
        }

        // 检查该 QQ 是否已被其他用户绑定
        if (userRepository.existsByQqNumber(qqId)) {
            throw new BusinessException(409, "该 QQ 号已被其他用户绑定");
        }

        // 扫所有 bind:code:* 找匹配的 code
        String matchedUsername = findUsernameByCode(code);
        if (matchedUsername == null) {
            throw new BusinessException(400, "验证码无效或已过期");
        }

        User user = userRepository.findByUsername(matchedUsername)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));

        // 二次检查：用户当前不应该已经有 qqNumber
        if (user.getQqNumber() != null && !user.getQqNumber().isBlank()) {
            redisTemplate.delete(CODE_PREFIX + matchedUsername);
            throw new BusinessException(400, "该账号已绑定过 QQ，请先解绑");
        }

        user.setQqNumber(qqId);
        userRepository.save(user);
        redisTemplate.delete(CODE_PREFIX + matchedUsername);
        log.info("用户 {} 成功绑定 QQ {}", matchedUsername, qqId);

        return new BindQqResponse(qqId, user.getUsername(), true);
    }

    /**
     * 前端调用：查询当前用户绑定状态
     */
    public BindQqResponse getBindStatus(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));
        if (user.getQqNumber() == null || user.getQqNumber().isBlank()) {
            return BindQqResponse.unbound();
        }
        return new BindQqResponse(user.getQqNumber(), user.getUsername(), true);
    }

    /**
     * 前端调用：解绑
     */
    @Transactional
    public BindQqResponse unbind(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));
        if (user.getQqNumber() == null || user.getQqNumber().isBlank()) {
            throw new BusinessException(400, "当前未绑定 QQ");
        }
        String oldQq = user.getQqNumber();
        user.setQqNumber(null);
        userRepository.save(user);
        log.info("用户 {} 解绑 QQ {}", username, oldQq);
        return BindQqResponse.unbound();
    }

    /**
     * 内部辅助：通过 code 找匹配的 username
     * 遍历所有 bind:code:* 键，SCAN 而不是 KEYS（生产环境安全）
     */
    private String findUsernameByCode(String code) {
        // 用 SCAN 避免阻塞 Redis
        org.springframework.data.redis.core.Cursor<String> cursor = redisTemplate.scan(
                org.springframework.data.redis.core.ScanOptions.scanOptions()
                        .match(CODE_PREFIX + "*")
                        .count(100)
                        .build()
        );
        try {
            while (cursor.hasNext()) {
                String key = cursor.next();
                String stored = redisTemplate.opsForValue().get(key);
                if (code.equals(stored)) {
                    return key.substring(CODE_PREFIX.length());
                }
            }
        } finally {
            cursor.close();
        }
        return null;
    }

    /**
     * 内部接口（Python webhook 调用）：通过 QQ 号查 user
     * Returns: {userId, username, bound} 或 null
     */
    public java.util.Map<String, Object> lookupByQqId(String qqId) {
        if (qqId == null || qqId.isBlank()) return null;
        return userRepository.findByQqNumber(qqId)
                .map(u -> {
                    java.util.Map<String, Object> m = new java.util.HashMap<>();
                    m.put("userId", u.getId());
                    m.put("username", u.getUsername());
                    m.put("bound", true);
                    return m;
                })
                .orElse(null);
    }

    /**
     * 内部接口（Python 调用）：通过 username 查自选股
     * TODO: 当前直接走 Repository；后续可改为专用的 internal API
     */
    public java.util.List<java.util.Map<String, Object>> getFavoritesByUsername(String username) {
        User user = userRepository.findByUsername(username).orElse(null);
        if (user == null) return java.util.Collections.emptyList();
        // 暂时返回空 list：自选股需要走 stocks/favorites 完整流程（包括 Redis 缓存）
        // 这里直接查 Repository 不走缓存会导致数据不一致
        // 暂时返回空，等用户调用方提供 userId 走 /api/v1/stocks/favorites
        return java.util.Collections.emptyList();
    }
}
