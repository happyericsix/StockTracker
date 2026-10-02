package com.happyericsix.stocktracker.job;

import com.happyericsix.stocktracker.repository.NewsEventRepository;
import com.happyericsix.stocktracker.service.NewsRetention;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;

import java.time.LocalDateTime;
import java.util.Collection;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyCollection;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 时效清理的删除顺序与保留期换算：钉住"披露类不被分类规则误删"的关键行为。
 */
class NewsRetentionJobTest {

    private NewsEventRepository repository;
    private NewsRetentionJob job;
    private LocalDateTime now;

    @BeforeEach
    void setUp() {
        repository = mock(NewsEventRepository.class);
        when(repository.deleteBySourceLevelInAndPublishedAtBefore(anyCollection(), any()))
                .thenReturn(0L);
        when(repository.deleteByCategoryAndSourceLevelNotInAndPublishedAtBefore(
                anyString(), anyCollection(), any())).thenReturn(0L);
        when(repository.deleteByCategoryIsNullAndSourceLevelNotInAndPublishedAtBefore(
                anyCollection(), any())).thenReturn(0L);
        // 全默认值：快讯 3 天、普通分类 30 天、披露类 180 天
        job = new NewsRetentionJob(repository, new NewsRetention(
                3, 30, 30, 30, 30, 30, 30, 30, 180));
        now = LocalDateTime.now();
    }

    @Test
    void disclosureLevelsGetTheirOwnLongWindow() {
        job.purgeExpired();

        ArgumentCaptor<LocalDateTime> cutoff = ArgumentCaptor.forClass(LocalDateTime.class);
        verify(repository).deleteBySourceLevelInAndPublishedAtBefore(
                eq(List.of(1, 3)), cutoff.capture());
        long days = java.time.Duration.between(
                cutoff.getValue(), now).toDays();
        assertTrue(days >= 179 && days <= 180, "披露类应按 180 天保留，实际 " + days);
    }

    @Test
    void flashCategoryIsPurgedAfterThreeDays() {
        job.purgeExpired();

        ArgumentCaptor<LocalDateTime> cutoff = ArgumentCaptor.forClass(LocalDateTime.class);
        verify(repository).deleteByCategoryAndSourceLevelNotInAndPublishedAtBefore(
                eq("市场快讯"), eq(List.of(1, 3)), cutoff.capture());
        long days = java.time.Duration.between(cutoff.getValue(), now).toDays();
        assertTrue(days >= 2 && days <= 3, "快讯应按 3 天保留，实际 " + days);
    }

    @Test
    void everyBrowsingCategoryIsPurgedExactlyOnce() {
        job.purgeExpired();

        // 7 个浏览分类 + 1 次披露类 + 1 次未分类兜底
        verify(repository, times(7)).deleteByCategoryAndSourceLevelNotInAndPublishedAtBefore(
                anyString(), anyCollection(), any());
        verify(repository, times(1)).deleteBySourceLevelInAndPublishedAtBefore(
                anyCollection(), any());
        verify(repository, times(1)).deleteByCategoryIsNullAndSourceLevelNotInAndPublishedAtBefore(
                anyCollection(), any());
    }

    @Test
    void repositoryFailureDoesNotPropagate() {
        when(repository.deleteBySourceLevelInAndPublishedAtBefore(anyCollection(), any()))
                .thenThrow(new RuntimeException("db down"));
        // 清理失败只记日志，不许把调度线程炸掉
        job.purgeExpired();
        verify(repository).deleteBySourceLevelInAndPublishedAtBefore(anyCollection(), any());
    }

    @Test
    void categoryDeletesAlwaysExcludeDisclosureLevels() {
        job.purgeExpired();

        @SuppressWarnings("unchecked")
        ArgumentCaptor<Collection<Integer>> levels = ArgumentCaptor.forClass(Collection.class);
        verify(repository, times(7)).deleteByCategoryAndSourceLevelNotInAndPublishedAtBefore(
                anyString(), levels.capture(), any());
        for (Collection<Integer> captured : levels.getAllValues()) {
            assertEquals(List.of(1, 3), List.copyOf(captured),
                    "分类删除必须排除公告/研报，否则 180 天的公告会被 30 天的分类规则误删");
        }
    }
}
