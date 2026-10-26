package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.MemorySkill;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface MemorySkillRepository extends JpaRepository<MemorySkill, Long> {

    List<MemorySkill> findByUserIdOrderByIdDesc(Long userId);

    /** 删除草稿时的链条保护：被更新版本 supersedes 的行不删（否则链条悬空）。 */
    boolean existsBySupersedesId(Long supersedesId);
}
