package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.entity.FavoriteStock;
import com.happyericsix.stocktracker.entity.Message;
import com.happyericsix.stocktracker.entity.NewsEvent;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.FavoriteStockRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.Spy;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

/**
 * 新闻预警的触发口径（2026-10-16）：
 * 高影响 + 方向非中性 + 置信度达标 + 命中自选股（6 位裸代码比较，前缀形态差异不漏）
 * → 写一条 NEWS_ALERT（幂等键 news-alert:{event}:{user}）+ 手机推送旁路。
 * 任何一条不满足都静默跳过——宁可少推，不可骚扰。
 */
@ExtendWith(MockitoExtension.class)
class NewsAlertServiceTest {

    @Mock
    private FavoriteStockRepository favoriteStockRepository;
    @Mock
    private UserRepository userRepository;
    @Mock
    private MessageService messageService;
    @Mock
    private PushService pushService;
    @InjectMocks
    private NewsAlertService newsAlertService;

    private final User alice = User.builder().id(7L).username("alice")
            .password("x").email("a@b.c").build();

    private NewsEvent event(String impact, String direction, Double confidence,
                            String symbol, String relatedJson) {
        NewsEvent e = NewsEvent.builder()
                .title("重大合同公告").name("江淮汽车")
                .plainSummary("签署重大销售合同，金额占上年营收 15%。")
                .impactLevel(impact).direction(direction).confidence(confidence)
                .symbol(symbol).relatedSymbols(relatedJson)
                .build();
        // builder 没有 id 的场景由用例自行反射塞（实体 @Data 有 setter）
        e.setId(101L);
        return e;
    }

    private void watchAlice(String... symbols) {
        when(userRepository.findAll()).thenReturn(List.of(alice));
        List<FavoriteStock> favorites = java.util.Arrays.stream(symbols)
                .map(s -> FavoriteStock.builder().stockSymbol(s).user(alice).build())
                .toList();
        when(favoriteStockRepository.findByUserId(7L)).thenReturn(favorites);
    }

    @Test
    void highImpactWatchedSymbolSendsMessageAndPush() {
        watchAlice("SH600418");   // 自选带前缀形态；事件侧用裸代码，也应命中
        NewsEvent e = event("high", "利好", 0.8, "600418", "[]");
        newsAlertService.notifyWatchers(e);

        ArgumentCaptor<String> key = ArgumentCaptor.forClass(String.class);
        ArgumentCaptor<String> content = ArgumentCaptor.forClass(String.class);
        verify(messageService).saveReport(eq(alice), eq("NEWS_ALERT"),
                key.capture(), eq("SH600418"), content.capture());
        assertEquals("news-alert:101:7", key.getValue());
        assertTrue(content.getValue().contains("利好"));
        assertTrue(content.getValue().contains("江淮汽车"));
        verify(pushService).push(eq("alice"), anyString(), anyString());
    }

    @Test
    void relatedSymbolsAlsoCountAsWatched() {
        watchAlice("SH000625");
        NewsEvent e = event("high", "利空", 0.7, null, "[\"SH000625\", \"SZ300750\"]");
        newsAlertService.notifyWatchers(e);
        verify(messageService).saveReport(eq(alice), eq("NEWS_ALERT"),
                eq("news-alert:101:7"), eq("SH000625"), anyString());
    }

    @Test
    void mediumImpactOrNeutralOrLowConfidenceNeverAlerts() {
        // 不打自选桩：这四条都在 shouldAlert 就返回，根本走不到查自选——
        // 打了桩反而触发 Mockito 严格模式的 UnnecessaryStubbing
        newsAlertService.notifyWatchers(event("medium", "利好", 0.9, "600418", "[]"));
        newsAlertService.notifyWatchers(event("high", "中性", 0.9, "600418", "[]"));
        newsAlertService.notifyWatchers(event("high", "利好", 0.3, "600418", "[]"));
        newsAlertService.notifyWatchers(event("high", null, 0.9, "600418", "[]"));
        verify(messageService, never()).saveReport(any(), anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void notWatchedSymbolNeverAlerts() {
        watchAlice("600519");
        newsAlertService.notifyWatchers(event("high", "利好", 0.9, "600418", "[]"));
        verify(messageService, never()).saveReport(any(), anyString(), anyString(), anyString(), anyString());
    }

    @Test
    void saveReportFailureDoesNotBreakOtherUsers() {
        when(userRepository.findAll()).thenReturn(List.of(alice));
        when(favoriteStockRepository.findByUserId(7L))
                .thenReturn(List.of(FavoriteStock.builder().stockSymbol("600418").user(alice).build()));
        when(messageService.saveReport(any(), anyString(), anyString(), anyString(), anyString()))
                .thenThrow(new RuntimeException("db down"));
        newsAlertService.notifyWatchers(event("high", "利好", 0.9, "600418", "[]"));
        // 不抛异常即通过：旁路失败只记日志，绝不影响解读落库链路
    }
}
