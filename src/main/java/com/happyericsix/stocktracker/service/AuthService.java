package com.happyericsix.stocktracker.service;

import com.happyericsix.stocktracker.dto.AuthResponse;
import com.happyericsix.stocktracker.dto.LoginRequest;
import com.happyericsix.stocktracker.dto.RegisterRequest;
import com.happyericsix.stocktracker.entity.Role;
import com.happyericsix.stocktracker.entity.User;
import com.happyericsix.stocktracker.repository.UserRepository;
import org.springframework.security.authentication.AuthenticationManager;
import org.springframework.security.authentication.UsernamePasswordAuthenticationToken;
import org.springframework.security.core.Authentication;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

@Service
public class AuthService {

    private final UserRepository userRepository;
    private final PasswordEncoder passwordEncoder;
    private final AuthenticationManager authenticationManager;
    private final TokenService tokenService;

    public AuthService(UserRepository userRepository,
                       PasswordEncoder passwordEncoder,
                       AuthenticationManager authenticationManager,
                       TokenService tokenService) {
        this.userRepository = userRepository;
        this.passwordEncoder = passwordEncoder;
        this.authenticationManager = authenticationManager;
        this.tokenService = tokenService;
    }

    public AuthResponse register(RegisterRequest request) {
        if (userRepository.existsByUsername(request.getUsername())) {
            throw new IllegalArgumentException("用户名已存在");
        }

        User user = User.builder()
                .username(request.getUsername())
                .password(passwordEncoder.encode(request.getPassword()))
                .email(request.getEmail() != null ? request.getEmail() : request.getUsername() + "@qq.com")
                .qqNumber(null)  // QQ 号需用户登录后单独绑定，不再与 username 绑定
                .role(Role.USER)
                .build();

        userRepository.save(user);

        Authentication authentication = authenticationManager.authenticate(
                new UsernamePasswordAuthenticationToken(request.getUsername(), request.getPassword()));

        String token = tokenService.generateToken(authentication);

        return new AuthResponse(token, user.getUsername(), user.getEmail(), "注册成功");
    }

    public AuthResponse login(LoginRequest request) {
        Authentication authentication = authenticationManager.authenticate(
                new UsernamePasswordAuthenticationToken(request.getUsername(), request.getPassword()));

        String token = tokenService.generateToken(authentication);

        User user = userRepository.findByUsername(request.getUsername())
                .orElseThrow(() -> new IllegalArgumentException("用户不存在"));

        return new AuthResponse(token, user.getUsername(), user.getEmail(), "登录成功");
    }

}
