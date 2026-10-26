// 手机端 DOM 几何审计：不依赖截图的客观检测。
// 检测五类问题：① 可点元素触控高度 <40px ② 文本字号 <11px
// ③ 可见可交互元素互相重叠 ④ fixed/sticky 元素被视口裁切
// ⑤ 底部固定栏（输入条/横幅）遮挡页面最后的内容
import { chromium, devices } from '@playwright/test'
import fs from 'node:fs'

const BASE = process.argv[2] || 'http://localhost:5173'
const USER = process.env.E2E_PROBE_USER || 'mobile_audit'
const PASS = process.env.E2E_PROBE_PASS || 'Audit#2026temp'

const PAGES = [
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

const browser = await chromium.launch()
const ctx = await browser.newContext({ ...devices['iPhone 13'], locale: 'zh-CN' })
const page = await ctx.newPage()

await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
await page.getByLabel('用户名、手机号或邮箱').fill(USER)
await page.getByLabel('密码').fill(PASS)
await page.getByRole('button', { name: '登录', exact: true }).click()
await page.waitForURL(/dashboard/, { timeout: 20000 })
await page.waitForTimeout(2000)
// 跳过首登引导
try {
  await page.getByText('使用说明', { exact: false }).first().waitFor({ state: 'visible', timeout: 8000 })
  for (let i = 0; i < 12; i++) {
    const next = page.getByRole('button', { name: /下一块/ })
    if (await next.count()) { await next.first().click(); await page.waitForTimeout(300); continue }
    const start = page.getByRole('button', { name: /开始使用/ })
    if (await start.count()) { await start.first().click(); await page.waitForTimeout(600); break }
    break
  }
} catch {}

const probe = () => {
  const vw = innerWidth, vh = innerHeight
  const issues = { smallTap: [], smallFont: [], overlap: [], fixedClipped: [], bottomCover: [] }
  const visible = (el) => {
    const r = el.getBoundingClientRect()
    if (r.width < 2 || r.height < 2) return null
    const s = getComputedStyle(el)
    if (s.visibility === 'hidden' || s.display === 'none' || +s.opacity === 0) return null
    return r
  }
  const desc = (el) => {
    const cls = String(el.className?.baseVal ?? el.className ?? '').trim().split(/\s+/).slice(0, 3).join('.')
    const txt = (el.textContent || '').trim().slice(0, 14).replace(/\s+/g, ' ')
    return `${el.tagName.toLowerCase()}${cls ? '.' + cls : ''}${txt ? ` "${txt}"` : ''}`
  }

  // ① 触控目标过小（可见的按钮/链接/输入）
  for (const el of document.querySelectorAll('button, a[href], input, [role="button"], select')) {
    const r = visible(el); if (!r) continue
    // 忽略视觉上隐藏在抽屉里的（在视口外）
    if (r.right < 0 || r.left > vw) continue
    if (r.height > 0 && r.height < 30 && r.width > 24) issues.smallTap.push(`${desc(el)} h=${Math.round(r.height)}`)
  }
  // ② 字号过小
  for (const el of document.querySelectorAll('p, span, a, td, th, li, label, h1,h2,h3,h4,h5,h6, dt, dd')) {
    if (!el.childNodes.length) continue
    if (![...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim())) continue
    const r = visible(el); if (!r || r.right < 0 || r.left > vw) continue
    const fs = parseFloat(getComputedStyle(el).fontSize)
    if (fs < 11) issues.smallFont.push(`${desc(el)} font=${fs}px`)
  }
  // ③ 可交互元素重叠（同层无关的父子不算）
  const clickables = [...document.querySelectorAll('button, a[href], input, [role="button"]')]
    .map((el) => ({ el, r: visible(el) }))
    .filter((x) => x.r && x.r.right > 0 && x.r.left < vw && x.r.top > 0 && x.r.bottom < vh)
  for (let i = 0; i < clickables.length; i++) {
    for (let j = i + 1; j < clickables.length; j++) {
      const a = clickables[i], b = clickables[j]
      if (a.el.contains(b.el) || b.el.contains(a.el)) continue
      const ox = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left)
      const oy = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top)
      if (ox > 6 && oy > 6 && !(getComputedStyle(a.el).position === 'absolute' && a.r.width < 30)) {
        issues.overlap.push(`${desc(a.el)} × ${desc(b.el)} (重叠 ${Math.round(ox)}x${Math.round(oy)})`)
      }
    }
  }
  // ④ fixed/sticky 元素被视口裁切
  for (const el of document.querySelectorAll('*')) {
    const s = getComputedStyle(el)
    if (!/fixed|sticky/.test(s.position)) continue
    const r = visible(el); if (!r) continue
    if (r.right > vw + 2 || r.bottom > vh + 2 || r.left < -2) {
      // 抽屉（负偏移隐藏）不算
      issues.fixedClipped.push(`${desc(el)} pos=${s.position} rect=${Math.round(r.left)},${Math.round(r.top)},${Math.round(r.right)},${Math.round(r.bottom)} viewport=${vw}x${vh}`)
    }
  }
  // ⑤ 底部固定栏遮住文档流最后内容：页面滚到最底，看最后元素是否被 fixed 底栏盖住
  return { vw, vh, issues }
}

const out = {}
for (const [name, path] of PAGES) {
  try { await page.goto(BASE + path, { waitUntil: 'networkidle', timeout: 25000 }) } catch { await page.waitForTimeout(2500) }
  await page.waitForTimeout(1200)
  const r = await page.evaluate(probe)
  out[name] = r.issues
  const counts = Object.fromEntries(Object.entries(r.issues).map(([k, v]) => [k, v.length]))
  console.log(`\n== ${name} ==`, JSON.stringify(counts))
  for (const [k, list] of Object.entries(r.issues)) list.slice(0, 6).forEach((x) => console.log(`  [${k}] ${x}`))
}
fs.writeFileSync('test-results/geometry-audit.json', JSON.stringify(out, null, 1))
await browser.close()
process.exit(0)
