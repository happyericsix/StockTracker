package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.JobRun;
import com.happyericsix.stocktracker.repository.JobRunRepository;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 任务心跳的**写入侧**：这是本地监控（server-monitor 读 job_runs 表）的数据源，
 * 它的行为契约是"任务跑完必须留下正确的点名行"。
 *
 * <p>红黄灯的判定在消费侧（本地监控）实现，这里不重复 —— 数据在谁手里，
 * 判定跟到谁；Java 侧只负责把点名写对。
 */
class JobRunServiceTest {

    private final JobRunRepository repository = mock(JobRunRepository.class);
    private final JobRunService service = new JobRunService(repository);

    @Test
    void recordNormalizesStatusAndClipsDetail() {
        service.record("paper-daily", "ok", "一切正常");
        service.record("paper-daily", "weird-status", "x".repeat(600));

        ArgumentCaptor<JobRun> captor = ArgumentCaptor.forClass(JobRun.class);
        verify(repository, times(2)).save(captor.capture());
        assertEquals(JobRunService.STATUS_OK, captor.getAllValues().get(0).getStatus());
        assertEquals("一切正常", captor.getAllValues().get(0).getDetail());
        // 未知状态按失败申报（宁可误报红，不可把失败藏成绿）
        assertEquals(JobRunService.STATUS_FAILED, captor.getAllValues().get(1).getStatus());
        assertTrue(captor.getAllValues().get(1).getDetail().length() <= 500,
                "detail 必须截断到列宽内");
    }

    @Test
    void recordSwallowsRepositoryFailure() {
        when(repository.save(any())).thenThrow(new RuntimeException("db down"));
        // 心跳挂了不能拖死任务本身 —— 这一行不抛异常就是断言
        service.record("paper-daily", "ok", null);
    }

    @Test
    void runWithHeartbeatRecordsOkAndFailed() {
        service.runWithHeartbeat("news-topics", () -> "13 条事件线");
        service.runWithHeartbeat("news-topics", () -> {
            throw new IllegalStateException("boom");
        });

        ArgumentCaptor<JobRun> captor = ArgumentCaptor.forClass(JobRun.class);
        verify(repository, times(2)).save(captor.capture());
        assertEquals(JobRunService.STATUS_OK, captor.getAllValues().get(0).getStatus());
        assertEquals("13 条事件线", captor.getAllValues().get(0).getDetail());
        assertEquals(JobRunService.STATUS_FAILED, captor.getAllValues().get(1).getStatus());
        assertTrue(captor.getAllValues().get(1).getDetail().contains("boom"));
    }
}
