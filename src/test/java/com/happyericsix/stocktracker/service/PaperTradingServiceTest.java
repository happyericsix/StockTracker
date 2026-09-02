package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.entity.PaperAccount;
import com.happyericsix.stocktracker.entity.PaperTrade;
import com.happyericsix.stocktracker.entity.Strategy;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.PaperAccountRepository;
import com.happyericsix.stocktracker.repository.PaperTradeRepository;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.springframework.transaction.PlatformTransactionManager;
import tools.jackson.databind.JsonNode;
import tools.jackson.databind.ObjectMapper;

import java.time.LocalDate;
import java.util.List;
import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.ArgumentMatchers.isNull;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.times;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class PaperTradingServiceTest {

    @Mock
    private StrategyRepository strategyRepository;

    @Mock
    private PaperAccountRepository paperAccountRepository;

    @Mock
    private PaperTradeRepository paperTradeRepository;

    @Mock
    private StrategyClient strategyClient;

    @Mock
    private UserRepository userRepository;

    @Mock
    private PlatformTransactionManager transactionManager;

    @InjectMocks
    private PaperTradingService paperTradingService;

    private final ObjectMapper mapper = new ObjectMapper();

    @Test
    void buySignalCreatesTradeAndUpdatesAccount() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":0.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        Strategy strategy = Strategy.builder()
                .id(10L)
                .name("MACD cross")
                .symbol("AAPL")
                .configJson(configJson)
                .user(user)
                .paperEnabled(true)
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(false);
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("AAPL"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperAccount> accountCaptor = ArgumentCaptor.forClass(PaperAccount.class);
        verify(paperAccountRepository, times(2)).save(accountCaptor.capture());
        PaperAccount account = accountCaptor.getAllValues().get(1);
        assertTrue(account.getShares() > 0);
        assertTrue(account.getCash() < account.getInitialCapital());

        ArgumentCaptor<PaperTrade> tradeCaptor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(tradeCaptor.capture());
        PaperTrade trade = tradeCaptor.getValue();
        assertEquals("BUY", trade.getSide());
        assertEquals("ma_cross", trade.getReason());
        assertEquals(10.0, trade.getPrice(), 0.0001);
        assertEquals(1000.0, trade.getShares(), 0.0001);
    }

    @Test
    void positiveCommissionReducesShares() throws Exception {
        String configJson = "{\"initial_capital\":10000.0,"
                + "\"risk\":{\"commission_pct\":10.0,\"slippage_pct\":0.0},"
                + "\"position\":{\"type\":\"full\",\"size_pct\":100.0}}";
        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        Strategy strategy = Strategy.builder()
                .id(10L)
                .name("MACD cross")
                .symbol("AAPL")
                .configJson(configJson)
                .user(user)
                .paperEnabled(true)
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(false);
        when(paperAccountRepository.findByStrategyId(10L)).thenReturn(Optional.empty());
        when(paperAccountRepository.save(any(PaperAccount.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));
        when(paperTradeRepository.save(any(PaperTrade.class)))
                .thenAnswer(invocation -> invocation.getArgument(0));

        JsonNode result = mapper.readTree(
                "{\"signal\":\"buy\",\"price\":10.0,\"matched_conditions\":[\"ma_cross\"]}");
        when(strategyClient.evaluateBar(eq(configJson), eq("AAPL"), anyString(), isNull()))
                .thenReturn(result);

        paperTradingService.evaluateDaily();

        ArgumentCaptor<PaperAccount> accountCaptor = ArgumentCaptor.forClass(PaperAccount.class);
        verify(paperAccountRepository, times(2)).save(accountCaptor.capture());
        PaperAccount account = accountCaptor.getAllValues().get(1);
        assertEquals(900.0, account.getShares(), 0.0001);
        assertEquals(100.0, account.getCash(), 0.0001);

        ArgumentCaptor<PaperTrade> tradeCaptor = ArgumentCaptor.forClass(PaperTrade.class);
        verify(paperTradeRepository, times(1)).save(tradeCaptor.capture());
        PaperTrade trade = tradeCaptor.getValue();
        assertEquals(900.0, trade.getShares(), 0.0001);
        assertEquals(9000.0, trade.getAmount(), 0.0001);
    }

    @Test
    void sameDateIsIdempotent() {
        String configJson = "{\"initial_capital\":100000.0}";
        User user = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();
        Strategy strategy = Strategy.builder()
                .id(10L)
                .name("MACD cross")
                .symbol("AAPL")
                .configJson(configJson)
                .user(user)
                .paperEnabled(true)
                .build();

        when(strategyRepository.findByPaperEnabledTrue()).thenReturn(List.of(strategy));
        when(strategyRepository.findById(10L)).thenReturn(Optional.of(strategy));
        when(paperTradeRepository.existsByStrategyIdAndTradeDate(eq(10L), any(LocalDate.class)))
                .thenReturn(true);

        paperTradingService.evaluateDaily();

        verifyNoInteractions(strategyClient);
        verify(paperAccountRepository, never()).save(any(PaperAccount.class));
        verify(paperTradeRepository, never()).save(any(PaperTrade.class));
    }
}
