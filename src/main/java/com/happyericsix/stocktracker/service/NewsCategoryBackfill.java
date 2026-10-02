package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.repository.NewsEventRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import java.util.List;

/**
 * 存量资讯的文件夹分类回填（一次性，幂等）。
 *
 * <p>category 列上线时库里已有历史条目。新条目在 upsert 时就带分类，
 * 这里只补 {@code category IS NULL} 的旧行 —— 每次启动都跑，但只摸
 * 空行，回填完成后就是一次空查询，零成本。
 *
 * <p>用关键词规则而不是重放解读：解读要花模型调用，而旧条目的
 * 300 字摘要对规则分类已经足够；后续它们若被补解读，模型的
 * category 会照常覆写（applyAnalysis 的优先级在规则之上）。
 */
@Component
public class NewsCategoryBackfill implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(NewsCategoryBackfill.class);

    private final NewsEventRepository newsEventRepository;

    public NewsCategoryBackfill(NewsEventRepository newsEventRepository) {
        this.newsEventRepository = newsEventRepository;
    }

    @Override
    public void run(ApplicationArguments args) {
        try {
            List<NewsEvent> unclassified = newsEventRepository.findByCategoryIsNull();
            if (unclassified.isEmpty()) {
                return;
            }
            for (NewsEvent event : unclassified) {
                event.setCategory(NewsCategory.classify(
                        event.getSymbol(), event.getTitle(), event.getContent()));
                newsEventRepository.save(event);
            }
            log.info("资讯文件夹分类回填：{} 条存量条目已打标", unclassified.size());
        } catch (Exception e) {
            // 回填是增强件：失败只影响旧条目的文件夹归属，不能挡应用启动
            log.warn("资讯分类回填失败（旧条目暂无文件夹归属）：{}", e.getMessage());
        }
    }
}
