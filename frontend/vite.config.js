import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import tailwindcss from '@tailwindcss/vite'
import { VitePWA } from 'vite-plugin-pwa'
import { writeFileSync } from 'node:fs'

// 构建版本号（注入 __APP_VERSION__，BeianFooter 显示）：用户报"看不到新版本"时
// 一眼对齐"我跑的哪版 / 服务器最新哪版"，不用再猜更新有没有生效。
// 用东八区本地时间而非 toISOString（UTC 会和用户手表差 8 小时，对不上号）
const buildAt = new Date(Date.now() + 8 * 3600 * 1000)
const APP_VERSION = buildAt.toISOString().slice(0, 16).replace('T', ' ')

// dist/version.json：给**已经在运行的老版本**比对用（main.js checkVersion 每 2 分钟
// 拉一次）。刻意不在 workbox globPatterns 里（不含 json）——进了 SW 预缓存的
// version.json 会被老 SW 原样吐回，比对永远相等，功能就废了。
function versionJsonPlugin() {
  return {
    name: 'st-version-json',
    writeBundle() {
      writeFileSync(
        new URL('./dist/version.json', import.meta.url),
        JSON.stringify({ version: APP_VERSION }, null, 2),
      )
    },
  }
}

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
  },
  plugins: [
    versionJsonPlugin(),
    vue(),
    tailwindcss(),
    VitePWA({
      registerType: 'autoUpdate',
      // 注册逻辑在 src/main.js 显式实现（带 controllerchange 自动刷新）：
      // 插件自动注入的 registerSW.js 只注册不刷新，发版后已打开的页面
      // 会一直停在旧应用上（2026-10-16 "前端永远静态在旧版"的根因）
      injectRegister: false,
      includeAssets: ['favicon.svg'],
      manifest: {
        name: 'StockTracker',
        short_name: 'Stock',
        description: '量化股票分析助手',
        // id 是 PWA 的身份锚点：start_url 改动时靠它保持"同一个应用"
        id: '/',
        // 应用实际语言是中文，插件默认的 en 会让系统按英文应用处理
        lang: 'zh-CN',
        // 墨阶侧栏色（DESIGN.md --sidebar），独立模式下状态栏与壳连成一片
        theme_color: '#13151c',
        background_color: '#f5f6f8',
        display: 'standalone',
        orientation: 'portrait',
        start_url: '/',
        icons: [
          { src: 'icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: 'icon-512.png', sizes: '512x512', type: 'image/png' },
          // maskable：Android 自适应启动图标（无此条装机图标会被裁出白边）
          { src: 'icon-maskable-192.png', sizes: '192x192', type: 'image/png', purpose: 'maskable' },
          { src: 'icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' }
        ]
      },
      workbox: {
        globPatterns: ['**/*.{js,css,html,svg,png,woff2}'],
        // SPA 回退不能吃掉这些路径：SW 接管后，地址栏/工具直接访问 API 与
        // version.json 时必须拿到真实响应，而不是被塞回 index.html
        navigateFallbackDenylist: [/^\/api\//, /^\/version\.json$/, /^\/sw\.js$/],
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
        // 8080 被同机其它应用占用时，可用 API_PROXY_TARGET 指向备用端口后端。
        target: process.env.API_PROXY_TARGET || 'http://localhost:8080',
        changeOrigin: true
      }
    }
  }
})
