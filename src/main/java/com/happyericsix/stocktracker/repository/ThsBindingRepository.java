package com.happyericsix.stocktracker.repository;

import com.happyericsix.stocktracker.entity.ThsBinding;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.Optional;

@Repository
public interface ThsBindingRepository extends JpaRepository<ThsBinding, Long> {

    /** 一个用户最多一条绑定。 */
    Optional<ThsBinding> findByUserId(Long userId);

    boolean existsByUserId(Long userId);

    void deleteByUserId(Long userId);
}
