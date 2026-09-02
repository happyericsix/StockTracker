package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.client.StrategyClient;
import com.happyericsix.stocktracker.dto.StrategyRequest;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.StrategyRepository;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.util.Optional;

import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

@ExtendWith(MockitoExtension.class)
class StrategyServiceTest {

    @Mock
    private StrategyRepository strategyRepository;

    @Mock
    private StrategyClient strategyClient;

    @Mock
    private UserRepository userRepository;

    @InjectMocks
    private StrategyService strategyService;

    @Test
    void cannotUpdateOtherUsersStrategy() {
        User owner = User.builder()
                .id(1L)
                .username("alice")
                .password("secret")
                .email("alice@example.com")
                .build();

        when(userRepository.findByUsername("alice")).thenReturn(Optional.of(owner));
        when(strategyRepository.findByIdAndUserId(99L, owner.getId())).thenReturn(Optional.empty());

        StrategyRequest request = new StrategyRequest("foreign", "AAPL", "{}");

        assertThrows(IllegalArgumentException.class,
                () -> strategyService.updateStrategy("alice", 99L, request));

        verifyNoInteractions(strategyClient);
    }
}
