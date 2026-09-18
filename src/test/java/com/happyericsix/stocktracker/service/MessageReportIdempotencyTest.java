package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.MessageRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

/**
 * 报告的幂等**真的由数据库保证**吗？（不是"代码看起来查过一次"）
 *
 * <h3>为什么必须落到真实数据库上验一次</h3>
 * 单测里 {@code findByDedupeKey} 是替身，它永远"查得到"，所以测不出真正的问题：
 * <ul>
 *   <li>{@code dedupe_key} 上的唯一索引到底建起来没有（{@code ddl-auto=update} 会静默不加约束）；</li>
 *   <li>唯一索引允许多个 NULL，所以"聊天消息没有键"也必须依然能随便插；</li>
 *   <li>190 字符的键会不会超索引长度。</li>
 * </ul>
 *
 * <p><b>跑在哪个库上</b>：测试类路径的 {@code application.properties} 把数据源换成了
 * H2 内存库（见 {@code src/test/resources}），所以这里验证的是**JPA 映射与约束语义**，
 * 不是 MySQL 的字节级行为（索引长度上限、字符集）。生产用的是 MySQL，
 * 建表由启动时的 {@code ddl-auto=update} 完成 —— 这两层各自负责自己能负责的部分，
 * 别把 H2 上的绿当成 MySQL 的证明。
 *
 * <p>本用例**不能**用 {@code @Transactional} 回滚：{@code saveReport} 刻意跑在
 * 独立事务（REQUIRES_NEW）里 —— 它必须能在调用方事务失败时依然独立提交，
 * 所以数据是**真的落库**的，测试自己负责清理。
 */
@SpringBootTest
class MessageReportIdempotencyTest {

    private static final String KEY = "paper_daily:itest:2099-01-01";

    @Autowired
    private MessageService messageService;

    @Autowired
    private MessageRepository messageRepository;

    @Autowired
    private UserRepository userRepository;

    @AfterEach
    void cleanUp() {
        messageRepository.findByDedupeKey(KEY).ifPresent(messageRepository::delete);
    }

    private User user() {
        return userRepository.findAll().stream().findFirst()
                .orElseGet(() -> userRepository.save(User.builder()
                        .username("report-itest").password("x").email("report-itest@example.com").build()));
    }

    @Test
    void theSameReportKeyIsDeliveredExactlyOnce() {
        User user = user();

        Message first = messageService.saveReport(user, PaperReviewReportService.TYPE_PAPER_REPORT,
                KEY, "600519", "【模拟盘复盘】第一次");
        Message second = messageService.saveReport(user, PaperReviewReportService.TYPE_PAPER_REPORT,
                KEY, "600519", "【模拟盘复盘】第二次（重跑）");

        assertNotNull(first);
        assertEquals(first.getId(), second.getId(), "重跑必须命中同一条消息");
        assertEquals("【模拟盘复盘】第一次", second.getContent(),
                "已有报告的内容不能被重跑改写 —— 报告是当时那一刻的结论");
        assertEquals(1, messageRepository.findByDedupeKey(KEY).stream().count());
    }

    @Test
    void chatMessagesWithoutAKeyAreStillUnlimited() {
        // dedupe_key 可为 NULL，而 MySQL 的唯一索引允许多个 NULL —— 少了这件事，
        // 第二条聊天消息就会因为"键相同（都是 NULL）"插不进去
        User user = user();
        long before = messageRepository.count();

        messageService.saveAndPush(user, "CHAT_BOT", "测试消息 A", null);
        messageService.saveAndPush(user, "CHAT_BOT", "测试消息 B", null);

        assertTrue(messageRepository.count() >= before + 2);
        // 清理这两条测试消息
        messageRepository.findByUserIdOrderByCreatedAtDesc(user.getId()).stream()
                .filter(m -> m.getDedupeKey() == null && m.getContent() != null
                        && m.getContent().startsWith("测试消息"))
                .forEach(messageRepository::delete);
    }
}
