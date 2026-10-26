package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.UserLlmConfig;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

/**
 * 用户 LLM Key 配置（BYOK）。一个用户一行（唯一约束 user_id），与 ThsBinding 同构。
 */
public interface UserLlmConfigRepository extends JpaRepository<UserLlmConfig, Long> {

    Optional<UserLlmConfig> findByUserId(Long userId);

    void deleteByUserId(Long userId);
}
