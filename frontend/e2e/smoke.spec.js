import { test, expect } from '@playwright/test'

/**
 * 冒烟链路：登录 → 五个关键页面逐个"必须有真实内容"。
 *
 * 判定标准刻意不是"HTTP 200"（SPA 永远 200），而是**内容可见**：
 * 每页都断言一个只有渲染成功才会出现的元素。这就是能拦住
 * "嵌套 ref 白屏"那类事故的原因——白屏页面照样 200。
 *
 * 2026-10-13 墨阶重建：选择器随新版式更新（label 文案、button 化的信息流
 * 切换、侧栏品牌取代 .page 探针），判定语义不变。
 */

const USER = process.env.E2E_PROBE_USER || 'e2e_probe'
const PASS = process.env.E2E_PROBE_PASS || 'E2e#2026probe'

async function login(page) {
  await page.goto('/login')
  await page.getByLabel('用户名、手机号或邮箱').fill(USER)
  await page.getByLabel('密码').fill(PASS)
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/dashboard/)
}

test.beforeEach(async ({ page }) => {
  await login(page)
})

test('登录后首页渲染出自选行情标题与壳侧栏', async ({ page }) => {
  await page.goto('/dashboard')
  await expect(page.getByRole('heading', { name: '自选行情' })).toBeVisible()
  // 侧栏品牌存在 = AppShell 活着；导航链接可见 = 路由组件没炸
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible()
  await expect(page.getByRole('link', { name: '首页' })).toBeVisible()
})

test('模拟盘页渲染（无策略时是引导卡，不是空白）', async ({ page }) => {
  await page.goto('/paper')
  await expect(page.getByRole('heading', { name: '模拟盘' })).toBeVisible()
  // 要么策略卡（含"调整策略"动作）、要么"去创建"引导，且必须真的可见
  await expect(page.getByText(/模拟盘运行中|还没有任何策略/).first())
    .toBeVisible({ timeout: 15_000 })
})

test('资讯雷达页渲染出信息流切换与资讯列表', async ({ page }) => {
  await page.goto('/news')
  await expect(page.getByRole('heading', { name: '资讯雷达' })).toBeVisible()
  // 信息流切换是筛选按钮（同一列表的视图切换），不是 ARIA tab
  await expect(page.getByRole('button', { name: '大盘', exact: true })).toBeVisible()
})

test('个股 K 线页渲染出图表画布', async ({ page }) => {
  await page.goto('/chart/SH600519')
  // 行情冷缓存时应用会进入降级态（"暂无 K 线数据"），给一次重载机会：
  // 冒烟要拦的是"永远渲染不出来"，不是"上游冷启动慢"
  try {
    await page.locator('canvas').first().waitFor({ state: 'visible', timeout: 20_000 })
  } catch {
    await page.waitForTimeout(3_000)
    await page.reload()
    await expect(page.locator('canvas').first()).toBeVisible({ timeout: 30_000 })
  }
})

test('个人中心页渲染出消息推送配置卡', async ({ page }) => {
  await page.goto('/profile')
  await expect(page.getByRole('heading', { name: '个人中心' })).toBeVisible()
  await expect(page.getByRole('heading', { name: '消息推送' })).toBeVisible()
})

test('无效分享链接给出可读的失效提示（公开页不需要登录）', async ({ page }) => {
  // 手机视口下"退出登录"在抽屉导航里够不着，直接清 token 达到同一状态
  await page.evaluate(() => localStorage.removeItem('token'))
  await page.goto('/share/definitely-not-a-real-token')
  await expect(page.getByText('链接不存在或已失效')).toBeVisible({ timeout: 15_000 })
})
