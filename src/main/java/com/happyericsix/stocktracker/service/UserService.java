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