package com.happyericsix.stocktracker;

import com.happyericsix.stocktracker.util.CnTime;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.boot.context.properties.ConfigurationPropertiesScan;
import org.springframework.cache.annotation.EnableCaching;
import org.springframework.scheduling.annotation.EnableScheduling;

import java.util.TimeZone;

@EnableCaching
@EnableScheduling
@ConfigurationPropertiesScan
@SpringBootApplication
public class StocktrackerApplication {

    public static void main(String[] args) {
        // A 股业务时间必须按北京的日历走：交易日/冷却窗口/凭证过期在 UTC 容器里
        // 会整体漂移 8 小时（北京时间 0-8 点"今天"取成昨天）。所有未显式传
        // ZoneId 的 LocalDate/LocalDateTime.now()（含实体时间戳）都吃这个默认值；
        // 关键判定点另有 CnTime 常量兜底，这里是最外层的统一钉死。
        TimeZone.setDefault(TimeZone.getTimeZone(CnTime.ZONE));
        SpringApplication.run(StocktrackerApplication.class, args);
    }

}
