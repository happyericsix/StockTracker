import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'Stock Tracker',
        short_name: 'Stock',
        description: '量化股票分析助手',
        // 与页面顶部导航同色（--color-bg-inverse: #1a1a2e），
        // 独立模式下状态栏与 header 连成一片；旧值 #1a73e8 是第三个游离的蓝色。
        theme_color: '#1a1a2e',
        background_color: '#f0f2f5',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        runtimeCaching: [
          {
            // 资讯必须**不进缓存**：`NetworkFirst` 会在离线时用上一次的成功响应
            // 冒充新响应，而 5 分钟（maxAgeSeconds）对公告是致命的 ——
            // 用户会看到"今天没有新公告"，而其实是缓存没放行。
            // 放在通用规则之前：Workbox 按顺序匹配，先命中的赢。
            urlPattern: /^https?:\/\/.*\/api\/v1\/news\b.*/i,
            handler: 'NetworkOnly'
          },
          {
            // 行情类只读市场数据可以 NetworkFirst（离线时看到旧价好过白屏，
            // 且换账号也不构成串台风险）
            urlPattern: /^https?:\/\/.*\/api\/v1\/(stocks|market)\b.*/i,
            handler: 'NetworkFirst',
            options: {
              cacheName: 'market-api-cache',
              expiration: { maxEntries: 50, maxAgeSeconds: 300 }
            }
          },
          {
            // 其余 /api/ 一律 NetworkOnly：alerts/favorites/strategies/messages 是
            // 用户态数据，NetworkFirst 会在离线时回放旧列表，换账号登录后同一
            // 设备甚至可能看到上一账号的缓存
            urlPattern: /^https?:\/\/.*\/api\/.*/i,
            handler: 'NetworkOnly'
          }
        ]
      }
    })
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        // 8080 被同机其它应用占用时（曾发生：另一个 jar 抢占 8080，
        // 前端全部接口 404 却像"代码坏了"），可用 API_PROXY_TARGET 指向备用端口后端。
        target: process.env.API_PROXY_TARGET || 'http://localhost:8080',
        changeOrigin: true
      }
    }
  }
})
