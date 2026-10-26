import { test, expect } from '@playwright/test'

/**
 * 高频切换竞态回归钉（2026-10-19「频繁点击两个按钮加载缓慢/不全」事故）。
 *
 * 不依赖后端：page.route 把 /api/v1/** 全部拦截成固定桩件，K线/分钟接口
 * 故意加 300ms 延迟模拟真实上游（akshare 秒级响应的缩影）。
 *
 * 钉住三件事：
 *   1. 快速交替点击 周期按钮（日K↔分时）12 次，K线类请求必须被节流合并
 *      （旧代码每次点击整套重发 = 12 次；合并后应 ≤ 6 次）；
 *   2. 快速交替点击 显示（范围）按钮：纯本地切片，K线请求数必须**零增长**；
 *   3. 快速交替点击 类型（蜡烛/折线/OHLC）按钮：纯本地重画，零增长；
 *   以及终态正确：最后一次点击的周期处于激活态、画布仍在、无错误横幅。
 */

const SYMBOL = '600519'

/** 60 根日线，收盘价单调递增 —— 万一切错数据肉眼/断言可辨 */
function makeDayBars() {
  return Array.from({ length: 60 }, (_, i) => {
    const d = new Date(2026, 0, 1 + i)
    const iso = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
    const close = 1500 + i
    return {
      date: iso, open: String(close - 2), close: String(close),
      high: String(close + 3), low: String(close - 4), volume: String(10000 + i * 10),
    }
  })
}

/** 240 根分钟线 */
function makeMinuteBars() {
  return Array.from({ length: 240 }, (_, i) => {
    const close = 1500 + (i % 60)
    return {
      date: `2026-10-16 ${(9 + Math.floor(i / 60)).toString().padStart(2, '0')}:${(i % 60).toString().padStart(2, '0')}`,
      open: String(close - 1), close: String(close),
      high: String(close + 2), low: String(close - 2), volume: String(1000 + i),
    }
  })
}

/** 挂全部 API 桩件。返回计数器：kline = history+minute 请求次数（浏览器实际发起数） */
async function installMocks(page) {
  const counter = { kline: 0, history: 0, minute: 0 }
  const json = (data) => ({ status: 200, contentType: 'application/json', body: JSON.stringify(data) })
  const delayed = (data, ms) => new Promise((r) => setTimeout(() => r(json(data)), ms))

  // 兜底：未特判的接口一律回「成功空数据」，页面各 catch 分支能正常走完
  await page.route('**/api/v1/**', (route) =>
    route.fulfill(json({ code: 200, message: 'success', data: null })))

  // SSE（行情流/消息流）：回一个立刻结束的事件流 + retry:60s，避免测试期内重连风暴
  for (const p of ['**/api/v1/stocks/stream**', '**/api/v1/messages/stream**']) {
    await page.route(p, (route) => route.fulfill({
      status: 200, contentType: 'text/event-stream', body: 'retry: 60000\n\n',
    }))
  }

  await page.route(`**/api/v1/stocks/${SYMBOL}`, (route) =>
    route.fulfill(json({
      symbol: SYMBOL, name: '贵州茅台·桩', price: 1599, previousClose: 1590,
      change: 9, changePercent: 0.57, lastUpdated: '2026-10-16',
    })))

  await page.route(`**/api/v1/stocks/${SYMBOL}/history**`, async (route) => {
    counter.kline += 1
    counter.history += 1
    const bars = makeDayBars()
    return route.fulfill(await delayed({
      content: bars, page: 0, size: 1000, totalElements: bars.length, totalPages: 1,
    }, 300))
  })

  await page.route(`**/api/v1/stocks/${SYMBOL}/minute**`, async (route) => {
    counter.kline += 1
    counter.minute += 1
    return route.fulfill(await delayed(makeMinuteBars(), 300))
  })

  await page.route('**/api/v1/stocks/valuation**', (route) =>
    route.fulfill(json({ code: 200, message: 'success', data: { ok: false, items: [] } })))

  await page.route('**/api/v1/news/events**', (route) =>
    route.fulfill(json({ events: [], analyzing: false, analyzedCount: 0, pendingCount: 0 })))

  await page.route('**/api/v1/news/stock-read**', (route) =>
    route.fulfill(json({ code: 200, message: 'success', data: '综合解读（测试桩）' })))

  await page.route('**/api/v1/market/sentiment**', (route) => route.fulfill(json({ ok: false })))

  await page.route('**/api/v1/market/status**', (route) =>
    route.fulfill(json({ open: false, quoteDate: '2026-10-16', note: '休市（测试桩）' })))

  return counter
}

test('快速交替点击周期按钮：请求被合并、终态正确、无错误', async ({ page }) => {
  const counter = await installMocks(page)
  // 路由守卫只看 token 是否存在；API 全部被拦截，不会真正校验
  await page.addInitScript(() => localStorage.setItem('token', 'e2e-rapid-switch'))

  await page.goto(`/chart/${SYMBOL}`)
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 15_000 })
  expect(counter.kline).toBe(1)   // 初载一次

  // 12 次快速交替：日K → 分时 → 日K → …（旧代码 = 12 组整套重发）
  for (let i = 0; i < 12; i++) {
    await page.getByRole('button', { name: i % 2 === 0 ? '日K' : '分时', exact: true }).click()
    await page.waitForTimeout(30)
  }
  // 等尾沿重载发出并完成（尾沿 250ms + 桩件延迟 300ms）
  await page.waitForTimeout(1200)
  await expect(page.locator('canvas').first()).toBeVisible()

  // 合并断言：12 次点击远少于 12 次（节流 + 取消后，浏览器实际发起 ≤ 6 次）
  expect(counter.kline).toBeLessThanOrEqual(7)

  // 终态：最后点的是「分时」，按钮处于激活态；无错误横幅
  await expect(page.getByRole('button', { name: '分时', exact: true })).toHaveClass(/bg-accent/)
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('快速交替点击范围按钮：纯本地切片，零网络请求', async ({ page }) => {
  const counter = await installMocks(page)
  await page.addInitScript(() => localStorage.setItem('token', 'e2e-rapid-switch'))

  await page.goto(`/chart/${SYMBOL}`)
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 15_000 })
  const before = counter.kline

  // 分时模式下范围按钮是 1天/2天/5天/1周 —— 快速交替 10 次
  await page.getByRole('button', { name: '分时', exact: true }).click()
  await page.waitForTimeout(900)
  const beforeRange = counter.kline
  for (let i = 0; i < 10; i++) {
    await page.getByRole('button', { name: i % 2 === 0 ? '5天' : '1天', exact: true }).click()
    await page.waitForTimeout(30)
  }
  await page.waitForTimeout(600)
  expect(counter.kline).toBe(beforeRange)   // 范围切换一次网络请求都不该有
  expect(beforeRange - before).toBeLessThanOrEqual(1)   // 切分时本身的那一次
  await expect(page.locator('canvas').first()).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})

test('快速交替点击类型按钮：纯本地重画，零网络请求', async ({ page }) => {
  const counter = await installMocks(page)
  await page.addInitScript(() => localStorage.setItem('token', 'e2e-rapid-switch'))

  await page.goto(`/chart/${SYMBOL}`)
  await expect(page.locator('canvas').first()).toBeVisible({ timeout: 15_000 })

  for (let i = 0; i < 9; i++) {
    const name = ['蜡烛', '折线', 'OHLC'][i % 3]
    await page.getByRole('button', { name, exact: true }).click()
    await page.waitForTimeout(30)
  }
  await page.waitForTimeout(600)
  expect(counter.kline).toBe(1)   // 只有初载那一次；类型切换零请求
  await expect(page.locator('canvas').first()).toBeVisible()
  await expect(page.getByRole('alert')).toHaveCount(0)
})
