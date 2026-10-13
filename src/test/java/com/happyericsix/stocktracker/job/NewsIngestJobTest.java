package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.dto.MarketStatusResponse;
import com.happyericsix.stocktracker.repository.JobRunRepository;
import com.happyericsix.stocktracker.service.JobRunService;
import com.happyericsix.stocktracker.service.MarketStatusService;
import com.happyericsix.stocktracker.service.NewsService;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 资讯采集的节拍语义：钉住"交易时段快、休市慢、日历不可知宁可快"这套门控 ——
 * 这正是"休市也有新闻进来"与"别把 job_runs 刷爆"两个诉求的平衡点。
 */
class NewsIngestJobTest {

    private NewsService newsService;
    private MarketStatusService marketStatusService;
    private JobRunRepository jobRunRepository;
    private NewsIngestJob job;

    @BeforeEach
    void setUp() {
        newsService = mock(NewsService.class);
        marketStatusService = mock(MarketStatusService.class);
        jobRunRepository = mock(JobRunRepository.class);
        job = new NewsIngestJob(newsService, marketStatusService,
                new JobRunService(jobRunRepository));
    }

    private void phase(String value) {
        when(marketStatusService.current())
                .thenReturn(new MarketStatusResponse(
                        "2026-10-12", "MON", true, true, null, null, null,
                        !"closed".equals(value) && !"pre_open".equals(value), value, value,
                        null, null, null, null, null, null, null));
    }

    // ==================== wireDue：节拍门控 ====================

    @Test
    void tradingPhasesRunEveryTick() {
        for (String p : new String[]{"auction", "morning", "noon_break", "afternoon"}) {
            phase(p);
            assertTrue(job.wireDue(), "有行情阶段（" + p + "）每个 5 分钟刻度都该跑");
        }
    }

    @Test
    void closedPhaseThrottlesToSlowInterval() {
        phase("closed");
        // 刚跑过（首趟前 lastWireRun=0 会放行，所以先真实跑一趟再验节流）
        job.pullWire();
        assertFalse(job.wireDue(), "休市 + 刚跑过：30 分钟内不该再跑");
    }

    @Test
    void unknownCalendarFallsBackToFast() {
        when(marketStatusService.current()).thenThrow(new RuntimeException("日历不可达"));
        assertTrue(job.wireDue(), "日历拿不到时按快节拍：多拉无害（去重兜底），少拉漏新闻");
    }

    // ==================== pullWire：执行 + 心跳 ====================

    @Test
    void pullWireRefreshesAndWritesHeartbeat() {
        phase("morning");
        when(newsService.refreshWire()).thenReturn("快讯刷新完成：抓取 10 条，新入库 3 条");

        job.pullWire();

        verify(newsService).refreshWire();
        ArgumentCaptor<com.happyericsix.stocktracker.entity.JobRun> captor =
                ArgumentCaptor.forClass(com.happyericsix.stocktracker.entity.JobRun.class);
        verify(jobRunRepository).save(captor.capture());
        assertEquals(JobRunService.JOB_NEWS_WIRE, captor.getValue().getJobName(),
                "快讯趟必须写 news-wire 心跳（server-monitor 红黄灯依赖它）");
        assertEquals(JobRunService.STATUS_OK, captor.getValue().getStatus());
    }

    @Test
    void skippedTickWritesNothing() {
        phase("closed");
        job.pullWire();          // 首趟放行并执行
        job.pullWire();          // 30 分钟内：静默跳过

        verify(newsService).refreshWire();
        verify(jobRunRepository, org.mockito.Mockito.times(1)).save(any());
        // 静默跳过不是执行：不写心跳，也不把上一趟的 ok 覆盖成 failed
    }

    // ==================== 全量趟：复用手动刷新链路 ====================

    @Test
    void fullPassRefreshesAndWritesNewsRefreshHeartbeat() {
        when(newsService.refresh(null)).thenReturn("增量刷新完成");
        job.pullMorning();

        verify(newsService).refresh(null);
        ArgumentCaptor<com.happyericsix.stocktracker.entity.JobRun> captor =
                ArgumentCaptor.forClass(com.happyericsix.stocktracker.entity.JobRun.class);
        verify(jobRunRepository).save(captor.capture());
        assertEquals(JobRunService.JOB_NEWS_REFRESH, captor.getValue().getJobName(),
                "全量趟与手动刷新共用 news-refresh 心跳键：监控看到的是同一条管道");
    }

    @Test
    void failedRefreshRecordsFailedHeartbeat() {
        when(newsService.refresh(null)).thenThrow(new RuntimeException("python down"));
        job.pullEvening();

        ArgumentCaptor<com.happyericsix.stocktracker.entity.JobRun> captor =
                ArgumentCaptor.forClass(com.happyericsix.stocktracker.entity.JobRun.class);
        verify(jobRunRepository).save(captor.capture());
        assertEquals(JobRunService.STATUS_FAILED, captor.getValue().getStatus());
    }
}
