// 视口尺寸截图（贴近用户手机实际所见）：跳过引导后逐页截 390x844。
import { chromium, devices } from '@playwright/test'
import fs from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const USER = process.env.E2E_PROBE_USER || 'mobile_audit'
const PASS = process.env.E2E_PROBE_PASS || 'Audit#2026temp'

const PAGES = [
  ['login-logged-out', '/login'],
  ['dashboard', '/dashboard'],
  ['chart', '/chart/600519'],
  ['news', '/news'],
  ['assistant', '/assistant'],
  ['strategies', '/strategies'],
  ['paper', '/paper'],
  ['alerts', '/alerts'],
  ['messages', '/messages'],
  ['memory', '/memory'],
  ['profile', '/profile'],
]

fs.mkdirSync('test-results/mobile-vp', { recursive: true })
const browser = await chromium.launch()
const ctx = await browser.newContext({ ...devices['iPhone 13'], locale: 'zh-CN' })
const page = await ctx.newPage()

await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
await page.getByLabel('用户名、手机号或邮箱').fill(USER)
await page.getByLabel('密码').fill(PASS)
await page.getByRole('button', { name: '登录', exact: true }).click()
await page.waitForURL(/dashboard/, { timeout: 20000 })
await page.waitForTimeout(1500)

// 跳过首登引导：弹窗要等 profile 接口返回才挂载（AppShell.vue），
// 所以先等「使用说明」出现，再翻页直到「开始使用」
try {
  await page.getByText('使用说明', { exact: false }).first().waitFor({ state: 'visible', timeout: 10000 })
  for (let i = 0; i < 12; i++) {
    const next = page.getByRole('button', { name: /下一块/ })
    if (await next.count()) { await next.first().click(); await page.waitForTimeout(300); continue }
    const start = page.getByRole('button', { name: /开始使用/ })
    if (await start.count()) { await start.first().click(); await page.waitForTimeout(600); break }
    break
  }
} catch { /* 老账号没有引导 */ }

for (const [name, path] of PAGES) {
  if (name === 'login-logged-out') {
    await page.evaluate(() => localStorage.removeItem('token'))
  }
  try { await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 25000 }) } catch { await page.waitForTimeout(2500) }
  await page.waitForTimeout(1500)
  await page.screenshot({ path: `test-results/mobile-vp/${name}.png` }) // 视口尺寸
  if (name === 'login-logged-out') {
    // 重新登录
    await page.getByLabel('用户名、手机号或邮箱').fill(USER).catch(() => {})
    await page.getByLabel('密码').fill(PASS).catch(() => {})
    await page.getByRole('button', { name: '登录', exact: true }).click().catch(() => {})
    await page.waitForURL(/dashboard/, { timeout: 15000 }).catch(() => {})
  }
}
console.log('done')
await browser.close()
process.exit(0)
