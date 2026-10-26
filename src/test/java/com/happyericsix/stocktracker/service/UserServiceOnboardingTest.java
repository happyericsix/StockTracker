package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Map;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 新手引导状态的三态契约（onboardingCompleted）：
 * null = 上线前的老账号 → 对外当"已读"（不弹引导，e2e 探针账号也靠这条活路）；
 * false = 新注册 → 弹；true = 已读完。完成端点幂等（已是 true 不再写库）。
 */
@ExtendWith(MockitoExtension.class)
class UserServiceOnboardingTest {

    @Mock
    private UserRepository userRepository;
    @InjectMocks
    private UserService userService;

    private User user(Boolean onboarding) {
        return User.builder().id(1L).username("alice")
                .password("secret").email("alice@example.com")
                .onboardingCompleted(onboarding).build();
    }

    @Test
    void profileFoldsThreeStatesIntoBoolean() {
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user(null)));
        assertTrue((Boolean) userService.getProfile("alice").get("onboardingCompleted"),
                "null（老账号）对外折叠成 true：不打扰");

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user(false)));
        assertFalse((Boolean) userService.getProfile("alice").get("onboardingCompleted"),
                "false（新注册）= 要弹引导");

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(user(true)));
        assertTrue((Boolean) userService.getProfile("alice").get("onboardingCompleted"));
    }

    @Test
    void completeMarksTrueAndSaves() {
        User u = user(false);
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(u));
        userService.completeOnboarding("alice");
        assertEquals(Boolean.TRUE, u.getOnboardingCompleted());
        verify(userRepository).save(u);
    }

    @Test
    void completeIsIdempotentForAlreadyOnboardedUser() {
        User u = user(true);
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(u));
        userService.completeOnboarding("alice");
        verify(userRepository, never()).save(any());
    }

    @Test
    void legacyNullUserCompletesAndSaves() {
        // 老账号（null）主动重温后点完成：null → true 是一次真实的状态迁移，要落库
        User u = user(null);
        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(u));
        userService.completeOnboarding("alice");
        assertEquals(Boolean.TRUE, u.getOnboardingCompleted());
        verify(userRepository).save(u);
    }

    @Test
    void missingUserThrowsNotFound() {
        when(userRepository.findByUsername("ghost"))
                .thenReturn(Optional.empty());
        try {
            userService.completeOnboarding("ghost");
            throw new AssertionError("应该抛 404");
        } catch (com.happyericsix.stocktracker.exception.BusinessException e) {
            assertEquals(404, e.getCode());
        }
    }
}
