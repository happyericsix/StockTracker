package com.happyericsix.stocktracker.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import jakarta.persistence.Index;
import jakarta.persistence.Table;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

import java.time.LocalDate;
import java.time.LocalDateTime;

/**
 * 关键定时任务的**心跳行**：每次跑完写一行，回答"这个环节今天到底跑没跑、成没成"。
 *
 * <h3>为什么需要它（2026-10-09 事故）</h3>
 * 那天三个容器全程 running、首页全程 200，巡检脚本一路绿着 —— 但日线结算
 * 失败了一整天（数据源没出 bar），直到用户拿错误的日报截图来问才发现。
 * "进程活着"和"任务跑成了"是两件事，后者只能由任务自己申报。
 *
 * <h3>覆盖范围与刻意的边界</h3>
 * 只记**低频关键任务**（结算/重试/周复盘/专题/情绪快照/清理/新闻抓取）。
 * 高频循环（盘中盯盘 45s、价格刷新 15s、正文预抓 30s）不写表 —— 它们的
 * 健康状态用**数据新鲜度**表达（痕迹的 last_seen_at、行情时间），写表只会
 * 把自己刷成噪声。新闻抓取虽是用户访问触发而非 cron，也算关键活动：
 * 雷达页停止更新同样需要被看见。
 *
 * <h3>与痕迹表（paper_trade_traces）的分工</h3>
 * 痕迹回答"某一笔结算为什么是这个结果"（业务证据，面向复盘）；
 * 心跳回答"这一类任务今天有没有来过"（系统证据，面向运维）。
 * 前者是明细，后者是点名册。
 */
@Entity
@Table(name = "job_runs", indexes = {
        @Index(name = "idx_job_runs_name_finished", columnList = "jobName,finishedAt")
})
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class JobRun {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    /** 任务键（封闭集，见 {@code JobRunService.JOB_*}）：paper-daily / news-topics / ... */
    @Column(nullable = false, length = 50)
    private String jobName;

    /** 跑的当天（查询"今天该有的心跳"不用按时间窗猜）。 */
    @Column(nullable = false)
    private LocalDate runDate;

    /** ok / failed（封闭集，见 {@code JobRunService.STATUS_*}）。 */
    @Column(nullable = false, length = 10)
    private String status;

    /** 一句话摘要（计数/原因），给状态页的人看的，不是给日志看的。 */
    @Column(length = 500)
    private String detail;

    @Column(nullable = false)
    private LocalDateTime finishedAt;
}
