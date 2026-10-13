package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.NewsTopicRel;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Collection;
import java.util.List;

@Repository
public interface NewsTopicRelRepository extends JpaRepository<NewsTopicRel, Long> {

    /** 聚类匹配的入口：按成员事件 id 反查已有专题（topicId 靠共享成员数定归属）。 */
    List<NewsTopicRel> findByEventIdIn(Collection<Long> eventIds);

    /** 专题详情：成员事件 id（再由 NewsEventRepository.findAllById 取本体）。 */
    List<NewsTopicRel> findByTopicIdOrderByEventId(Long topicId);
}
