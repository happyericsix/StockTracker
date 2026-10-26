// 手机端布局实测：登录后走遍全部页面，检测横向溢出与超宽元素，逐页截图。
// 用法：node e2e/mobile-audit.js [baseURL]
import { chromium, devices } from '@playwright/test'
import fs from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const USER = process.env.E2E_PROBE_USER || 'e2e_probe'
const PASS = process.env.E2E_PROBE_PASS || 'E2e#2026probe'

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

fs.mkdirSync('test-results/mobile', { recursive: true })

const browser = await chromium.launch()
const ctx = await browser.newContext({ ...devices['iPhone 13'], locale: 'zh-CN' })
const page = await ctx.newPage()
const consoleErrors = []
page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 160)) })
page.on('pageerror', (e) => consoleErrors.push('PAGEERROR: ' + String(e).slice(0, 160)))

// 登录
await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
await page.getByLabel('用户名、手机号或邮箱').fill(USER)
await page.getByLabel('密码').fill(PASS)
await page.getByRole('button', { name: '登录', exact: true }).click()
await page.waitForURL(/dashboard/, { timeout: 20000 }).catch(() => console.log('!! 登录可能失败'))

const overflowProbe = () => {
  const vw = document.documentElement.clientWidth
  const docW = Math.max(
    document.documentElement.scrollWidth,
    document.body ? document.body.scrollWidth : 0,
  )
  const offenders = []
  const all = document.querySelectorAll('*')
  for (const el of all) {
    const r = el.getBoundingClientRect()
    if (r.width === 0 || r.height === 0) continue
    // 超出视口右缘 6px 容差；忽略在横向可滚容器内的
    if (r.right > vw + 6 || r.left < -6) {
      let p = el.parentElement, scrollable = false
      while (p && p !== document.body) {
        const s = getComputedStyle(p)
        if (/(auto|scroll)/.test(s.overflowX)) { scrollable = true; break }
        p = p.parentElement
      }
      if (!scrollable) {
        offenders.push({
          tag: el.tagName.toLowerCase(),
          cls: String(el.className && el.className.baseVal !== undefined ? el.className.baseVal : el.className || '').split(/\s+/).slice(0, 4).join('.'),
          right: Math.round(r.right), left: Math.round(r.left), w: Math.round(r.width),
        })
      }
    }
    if (offenders.length >= 12) break
  }
  return { vw, docW, overflow: docW > vw + 2, offenders }
}

const report = []
for (const [name, path] of PAGES) {
  try {
    await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 30000 })
  } catch { await page.waitForTimeout(3000) }
  await page.waitForTimeout(1200)
  const probe = await page.evaluate(overflowProbe)
  // 试试页面自身是否出现横向滚动条
  report.push({ name, path, ...probe })
  console.log(
    `${probe.overflow ? 'OVERFLOW' : '  ok    '} ${name.padEnd(18)} doc=${probe.docW}px vw=${probe.vw}px` +
      (probe.offenders.length ? `  超宽元素: ${probe.offenders.slice(0, 4).map((o) => `${o.tag}.${o.cls}@${o.right}`).join(' | ')}` : ''),
  )
  await page.screenshot({ path: `test-results/mobile/${name}.png`, fullPage: true })
}

fs.writeFileSync('test-results/mobile/report.json', JSON.stringify({ report, consoleErrors }, null, 1))
console.log('\n控制台错误数:', consoleErrors.length)
consoleErrors.slice(0, 8).forEach((e) => console.log('  -', e))
await browser.close()
process.exit(0)
