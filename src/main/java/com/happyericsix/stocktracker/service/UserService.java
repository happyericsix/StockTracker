package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.exception.BusinessException;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class UserService {

    private static final Logger log = LoggerFactory.getLogger(UserService.class);

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;

    public UserService(UserRepository userRepository, PasswordEncoder passwordEncoder) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
    }

    // ==================== 个人信息 ====================

    public Map<String, Object> getProfile(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));

        Map<String, Object> profile = new LinkedHashMap<>();
        profile.put("username", user.getUsername());
        profile.put("email", user.getEmail());
        profile.put("phone", user.getPhone());
        profile.put("pushChannel", pushChannelOrEmpty(user));
        profile.put("commissionPct", user.getCommissionPct());
        // 三态折叠成布尔下发：null（上线前的老账号）当已读——前端只关心"要不要弹引导"
        profile.put("onboardingCompleted", !Boolean.FALSE.equals(user.getOnboardingCompleted()));
        return profile;
    }

    /**
     * 新手引导完成：翻到最后一页并点了「开始使用」才调用。
     * 幂等——重复调用无害（老账号主动重温再关掉不会把状态改坏，本来就都是 true）。
     */
    @Transactional
    public void completeOnboarding(String username) {
        User user = userRepository.findByUsername(username)
                .orElseThrow(() -> new BusinessException(404, "用户不存在"));
        if (!Boolean.TRUE.equals(user.getOnboardingCompleted())) {
            user.setOnboardingCompleted(true);
            userRepository.save(user);
        }
    }

    /** pushChannel 对外只回显通道名；pushTarget 是密钥级的东西，不随 profile 下发。 */
    private static String pushChannelOrEmpty(User user) {
        String c = user.getPushChannel();
        return c == null ? "" : c;
    }

    private static final List<String> PUSH_CHANNELS = List.of("", "bark", "serverchan");

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
        if (updates.containsKey("commissionPct")) {
            Object raw = updates.get("commissionPct");
            if (raw == null || (raw instanceof String s && s.isBlank())) {
                user.setCommissionPct(null);   // 清空 = 回到系统默认
            } else {
                try {
                    double v = Double.parseDouble(String.valueOf(raw));
                    if (v <= 0 || v > 0.3) {
                        throw new NumberFormatException("out of range");
                    }
                    user.setCommissionPct(java.math.BigDecimal.valueOf(v));
                } catch (NumberFormatException e) {
                    throw new BusinessException(400,
                            "佣金率必须是 (0, 0.3] 的百分数，如 0.025 表示万 2.5");
                }
            }
        }
        if (updates.containsKey("pushChannel") || updates.containsKey("pushTarget")) {
            String channel = strOr(updates.get("pushChannel"), "");
            String target = strOr(updates.get("pushTarget"), "");
            if (!PUSH_CHANNELS.contains(channel)) {
                throw new BusinessException(400, "不支持的推送通道：" + channel);
            }
            // 换通道时旧目标一并清空：半套配置（新通道+旧 key）推出去只会是一堆失败请求。
            if (!channel.equals(pushChannelOrEmpty(user))) {
                user.setPushTarget("");
            }
            user.setPushChannel(channel);
            if (updates.containsKey("pushTarget")) {
                user.setPushTarget(target == null ? "" : target.trim());
            }
        }

        userRepository.save(user);
        log.info("用户 {} 更新了个人信息", username);
        return getProfile(username);
    }

    private static String strOr(Object v, String fallback) {
        return v instanceof String s ? s : fallback;
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