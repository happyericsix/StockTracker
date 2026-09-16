package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MemoryEvent;
import org.springframework.data.domain.Pageable;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.data.jpa.repository.Query;
import org.springframework.data.repository.query.Param;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * 记忆账本仓储。
 *
 * ⚠️ 这里<b>不提供</b>任何"修改内容"的 update/delete 派生方法，也不该加。
 * 账本是只追加的事件流，纠正靠追加新记录（M1 的 supersede 链），
 * 事实失效靠标记有效期；只有用户行使删除权时才走单独的硬删除通道。
 */
@Repository
public interface MemoryEventRepository extends JpaRepository<MemoryEvent, Long> {

    /** 一个会话的全部事件，按写入顺序（= 时间顺序） */
    List<MemoryEvent> findByUserIdAndSessionKeyOrderByIdAsc(Long userId, String sessionKey);

    long countByUserIdAndSessionKey(Long userId, String sessionKey);

    /**
     * 最近出现过的会话键（排除当前会话），用于判断"上一段对话是否还没总结"。
     * 会话键本身是 {@code userId:yyyy-MM-dd}，按字符串倒序即时间倒序。
     */
    @Query("select distinct e.sessionKey from MemoryEvent e "
            + "where e.userId = :userId and e.sessionKey <> :excludeSessionKey "
            + "order by e.sessionKey desc")
    List<String> findRecentOtherSessionKeys(@Param("userId") Long userId,
                                            @Param("excludeSessionKey") String excludeSessionKey,
                                            Pageable pageable);
}
