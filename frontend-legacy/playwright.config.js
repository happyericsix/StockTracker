import { defineConfig } from '@playwright/test'

/**
 * 冒烟 E2E：只回答一个问题——**关键页面还渲染得出来吗**。
 *
 * 诞生理由（2026-10-07 事故）：模板里嵌套取 ref 没解包，Dashboard/KLine
 * 主内容整页空白，而单测全绿、构建成功、照常上线。这类"渲染级"事故
 * 只有真的把页面打开才能拦住。
 *
 * 指向线上（BASE_URL 可覆盖为本地 dev server）：
 *   BASE_URL=http://localhost:5173 npx playwright test
 * 用只读探针账号（E2E_PROBE_USER / E2E_PROBE_PASS 环境变量，默认见下）。
 */
export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: process.env.BASE_URL || 'http://120.27.226.47',
    headless: true,
    viewport: { width: 390, height: 844 }, // 手机优先：错位最先出现在窄屏
    locale: 'zh-CN',
  },
  reporter: [['list']],
})
