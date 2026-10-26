// 安装横幅三档降级验证：iOS UA → 手动引导；微信 UA → 浏览器打开引导；桌面 → 不弹
import { chromium } from '@playwright/test'

const BASE = process.argv[2] || 'http://localhost:5173'

const cases = [
  {
    name: 'iPhone Safari UA',
    ua: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1',
    viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true,
    expect: '安装到主屏幕',
  },
  {
    name: 'Android 微信 UA',
    ua: 'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36 MicroMessenger/8.0.49',
    viewport: { width: 412, height: 915 }, isMobile: true, hasTouch: true,
    expect: '在浏览器中打开即可安装',
  },
  {
    name: '桌面 Chrome UA（不该弹）',
    ua: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
    viewport: { width: 1440, height: 900 },
    expect: null,
  },
]

const browser = await chromium.launch()
for (const c of cases) {
  const ctx = await browser.newContext({ userAgent: c.ua, viewport: c.viewport, isMobile: !!c.isMobile, hasTouch: !!c.hasTouch, locale: 'zh-CN' })
  const page = await ctx.newPage()
  await page.goto(BASE + '/login', { waitUntil: 'networkidle' })
  await page.waitForTimeout(5500) // 兜底展示延迟 4s + 渲染
  const bannerText = await page.evaluate(() => {
    const el = [...document.querySelectorAll('[role="dialog"][aria-label="安装提示"]')][0]
    return el ? el.textContent.replace(/\s+/g, ' ').trim() : null
  })
  const pass = c.expect
    ? bannerText && bannerText.includes(c.expect)
    : bannerText === null
  console.log(`${pass ? 'PASS' : 'FAIL'}  ${c.name}  ${bannerText ? '→ ' + bannerText.slice(0, 60) : '→ 无横幅'}`)
  await ctx.close()
}

// Login 页备案号检查
const ctx2 = await browser.newContext({ viewport: { width: 390, height: 844 }, locale: 'zh-CN' })
const p2 = await ctx2.newPage()
await p2.goto(BASE + '/login', { waitUntil: 'networkidle' })
await p2.waitForTimeout(800)
const hasBeian = await p2.evaluate(() => document.body.innerText.includes('浙ICP备'))
console.log(`${hasBeian ? 'PASS' : 'FAIL'}  Login 页展示备案号`)
await p2.screenshot({ path: 'test-results/login-beian.png' })

await browser.close()
process.exit(0)
