package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.BindQqResponse;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.exception.BusinessException;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.data.redis.core.StringRedisTemplate;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.security.SecureRandom;
import java.time.Duration;
import java.util.*;

@Service
public class UserService {

    private static final Logger log = LoggerFactory.getLogger(UserService.class);
    private static final String CODE_PREFIX = "bind:code:";
    private static final Duration CODE_TTL = Duration.ofMinutes(5);
    private static final SecureRandom RANDOM = new SecureRandom();

    private final UserRepository userRepository;
    private final StringRedisTemplate redisTemplate;
    private final PasswordEncoder passwordEncoder;

    public UserService(UserRepository userRepository, StringRedisTemplate redisTemplate,
                       PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.redisTemplate = redisTemplate;
        this.passwordEncoder = passwordEncoder;
    }

    // ==================== QQ 绑定 ====================

    public String generateBindCode(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));

        if (user.getQqNumber() != null && !user.getQqNumber().isBlank()) {
            throw new BusinessException(400, "已绑定 QQ：" + user.getQqNumber() + "，请先解绑");
        }

        String code = String.format("%06d", RANDOM.nextInt(1_000_000));
        String key = CODE_PREFIX + username;
        redisTemplate.opsForValue().set(key, code, CODE_TTL);
        log.info("用户 {} 生成 QQ 绑定验证码 {} (ttl=5min)", username, code);
        return code;
    }

    @Transactional
    public BindQqResponse verifyAndBind(String qqId, String code) {
        if (qqId == null || qqId.isBlank() || code == null || code.isBlank()) {
            throw new BusinessException(400, "qqId 和 code 不能为空");
        }
        if (!qqId.matches("\\d{5,12}")) {
            throw new BusinessException(400, "QQ 号格式错误（应为5-12位数字）");
        }
        if (!code.matches("\\d{6}")) {
            throw new BusinessException(400, "验证码格式错误（应为6位数字）");
        }
        if (userRepository.existsByQqNumber(qqId)) {
            throw new BusinessException(409, "该 QQ 号已被其他用户绑定");
        }

        String matchedUsername = findUsernameByCode(code);
        if (matchedUsername == null) {
            throw new BusinessException(400, "验证码无效或已过期");
        }

        User user = userRepository.findByUsername(matchedUsername)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));

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

    public BindQqResponse getBindStatus(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));
        if (user.getQqNumber() == null || user.getQqNumber().isBlank()) {
            return BindQqResponse.unbound();
        }
        return new BindQqResponse(user.getQqNumber(), user.getUsername(), true);
    }

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

    private String findUsernameByCode(String code) {
        org.springframework.data.redis.core.Cursor<String> cursor = redisTemplate.scan(
                org.springframework.data.redis.core.ScanOptions.scanOptions()
                        .match(CODE_PREFIX + "*").count(100).build());
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

    public Map<String, Object> lookupByQqId(String qqId) {
        if (qqId == null || qqId.isBlank()) return null;
        return userRepository.findByQqNumber(qqId)
                .map(u -> {
                    Map<String, Object> m = new HashMap<>();
                    m.put("userId", u.getId());
                    m.put("username", u.getUsername());
                    m.put("bound", true);
                    return m;
                }).orElse(null);
    }

    public List<Map<String, Object>> getFavoritesByUsername(String username) {
        return Collections.emptyList();
    }

    // ==================== 个人信息 ====================

    public Map<String, Object> getProfile(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));

        Map<String, Object> profile = new LinkedHashMap<>();
        profile.put("username", user.getUsername());
        profile.put("email", user.getEmail());
        profile.put("phone", user.getPhone());

        // 第三方绑定状态
        List<Map<String, Object>> bindings = new ArrayList<>();
        Map<String, Object> qqBinding = new LinkedHashMap<>();
        qqBinding.put("name", "QQ");
        qqBinding.put("icon", "🐧");
        qqBinding.put("bound", user.getQqNumber() != null && !user.getQqNumber().isBlank());
        qqBinding.put("account", user.getQqNumber() != null ? user.getQqNumber() : "");
        bindings.add(qqBinding);

        Map<String, Object> wxBinding = new LinkedHashMap<>();
        wxBinding.put("name", "微信");
        wxBinding.put("icon", "💬");
        wxBinding.put("bound", false);
        wxBinding.put("account", "");
        bindings.add(wxBinding);

        profile.put("bindings", bindings);
        return profile;
    }

    @Transactional
    public Map<String, Object> updateProfile(String username, Map<String, Object> updates) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));

        if (updates.containsKey("email")) {
            String email = (String) updates.get("email");
            if (email != null && !email.isBlank()) {
                user.setEmail(email);
            }
        }
        if (updates.containsKey("phone")) {
            String phone = (String) updates.get("phone");
            user.setPhone(phone);
        }

        userRepository.save(user);
        log.info("用户 {} 更新了个人信息", username);
        return getProfile(username);
    }

    @Transactional
    public void changePassword(String username, String oldPassword, String newPassword) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));

        if (!passwordEncoder.matches(oldPassword, user.getPassword())) {
            throw new BusinessException(400, "当前密码错误");
        }

        if (newPassword == null || newPassword.length() < 6) {
            throw new BusinessException(400, "新密码长度至少6位");
        }

        user.setPassword(passwordEncoder.encode(newPassword));
        userRepository.save(user);
        log.info("用户 {} 修改了密码", username);
    }
}
