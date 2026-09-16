package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.Message;
import org.springframework.data.domain.Page;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Modifying;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;
import java.util.Optional;

@Repository
public interface MessageRepository extends JpaRepository<Message, Long> {

    /** 消息中心：按时间倒序 */
    List<Message> findByUserIdOrderByCreatedAtDesc(Long userId);

    /** 消息中心：分页（按时间倒序） */
    Page<Message> findByUserId(Long userId, Pageable pageable);

    /** 消息中心：分页 + 按 type 过滤 */
    Page<Message> findByUserIdAndType(Long userId, String type, Pageable pageable);

    /** 消息中心：分页 + 时间范围 */
    Page<Message> findByUserIdAndCreatedAtGreaterThanEqual(Long userId, LocalDateTime since, Pageable pageable);

    /** 消息中心：分页 + type 过滤 + 时间范围 */
    Page<Message> findByUserIdAndTypeAndCreatedAtGreaterThanEqual(Long userId, String type, LocalDateTime since, Pageable pageable);

    /** 聊天历史：升序展示，类型限定 CHAT_USER / CHAT_BOT */
    List<Message> findByUserIdAndTypeInOrderByCreatedAtAsc(Long userId, List<String> types);

    /**
     * 聊天历史：只取最近 N 条（倒序），用于喂给 agent 的会话上下文。
     *
     * 不要用 {@link #findByUserIdAndTypeInOrderByCreatedAtAsc} 代替它：那个方法会把用户
     * 全部聊天记录一次读进内存，聊天越久越慢（每条消息都要付这个代价）。
     * N 取得比 ChatService.MAX_HISTORY_MESSAGES 大一些，留出"剔除当前消息"的余量。
     */
    List<Message> findTop20ByUserIdAndTypeInOrderByIdDesc(Long userId, List<String> types);

    /** 消息中心：按 type 过滤（如 type=ALERT 看预警历史） */
    List<Message> findByUserIdAndTypeOrderByCreatedAtDesc(Long userId, String type);

    long countByUserIdAndReadFalse(Long userId);

    /** 冷却判断：指定预警在 since 之后是否触发过 */
    boolean existsByAlertIdAndCreatedAtGreaterThan(Long alertId, LocalDateTime since);

    Optional<Message> findByIdAndUserId(Long id, Long userId);

    /** 全部已读；用原生 SQL 避免 read 列名歧义 */
    @Modifying
    @Query(value = "UPDATE messages SET is_read = true WHERE user_id = :userId AND is_read = false", nativeQuery = true)
    int markAllReadByUserId(@Param("userId") Long userId);
}
