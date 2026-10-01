package com.happyericsix.stocktracker.dto;

import com.happyericsix.stocktracker.util.CnTime;
import org.junit.jupiter.api.Test;

import java.time.LocalDateTime;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNull;

/**
 * 时效性分档（NewsEventResponse.freshnessLabel）。
 *
 * <p>分档的验收标准不是"档位边界精确到小时"（精确值有 freshHours），
 * 而是**人一眼能读懂**且**单调**：时间越久档位越旧，绝不能出现
 * "30 小时前显示 3小时内"这种倒挂——用户对"新鲜度"的信任一旦打破，
 * 整个可信度体系都会被连坐。
 */
class NewsEventResponseFreshnessTest {

    private static LocalDateTime hoursAgo(long hours) {
        return CnTime.now().minusHours(hours);
    }

    @Test
    void unknownTimeHasNoFreshness() {
        assertNull(NewsEventResponse.freshnessLabel(null));
        assertNull(NewsEventResponse.freshHours(null));
    }

    @Test
    void bandsAreHumanReadableAndMonotonic() {
        assertEquals("1小时内", NewsEventResponse.freshnessLabel(hoursAgo(0)));
        assertEquals("1小时内", NewsEventResponse.freshnessLabel(CnTime.now().minusMinutes(59)));
        assertEquals("3小时内", NewsEventResponse.freshnessLabel(hoursAgo(2)));
        assertEquals("6小时内", NewsEventResponse.freshnessLabel(hoursAgo(5)));
        assertEquals("24小时内", NewsEventResponse.freshnessLabel(hoursAgo(23)));
        assertEquals("3天前", NewsEventResponse.freshnessLabel(hoursAgo(24 * 3 + 5)));
        assertEquals("2周前", NewsEventResponse.freshnessLabel(hoursAgo(24 * 15)));
        assertEquals("30天前", NewsEventResponse.freshnessLabel(hoursAgo(24 * 100)));
    }

    @Test
    void futureTimestampsClampToZeroInsteadOfGoingNegative() {
        // 时钟偏差/源站时间略超前时，"负的新鲜度"比 0 更让人困惑
        assertEquals(Long.valueOf(0L), NewsEventResponse.freshHours(CnTime.now().plusMinutes(5)));
    }
}
