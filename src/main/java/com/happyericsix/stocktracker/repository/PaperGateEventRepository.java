package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.PaperGateEvent;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.time.LocalDateTime;
import java.util.List;

@Repository
public interface PaperGateEventRepository extends JpaRepository<PaperGateEvent, Long> {

    /** 升级计数：同一签名在窗口内的全部裁决（含 veto/hold——被否决的反复出现同样值得复盘）。 */
    List<PaperGateEvent> findByUserIdAndSignatureAndCreatedAtAfterOrderByCreatedAtAsc(
            Long userId, String signature, LocalDateTime since);

    /** 面板"近期计数"：给守门员自己看"这是本月第 N 次了"。 */
    List<PaperGateEvent> findByUserIdAndCreatedAtAfterOrderByCreatedAtDesc(
            Long userId, LocalDateTime since);
}
