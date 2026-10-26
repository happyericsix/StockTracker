// 线上 PWA 实测脚本（node e2e/pwa-live-check.js [baseURL]）：
//  1. SW 是否注册成功并接管页面
//  2. manifest <link> 与浏览器解析结果
//  3. 离线后整页刷新（NavigationRoute 回退）能否出壳 —— PWA 的核心承诺
//  4. 离线深链（SPA 路由）与在线截图留档
import { chromium } from '@playwright/test'

const BASE = process.argv[2] || 'https://stocktracker.com.cn'

;(async () => {
  const results = []
  const ok = (name, pass, detail) => {
    results.push({ name, pass, detail })
    console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`)
  }

  const browser = await chromium.launch()
  const context = await browser.newContext({ locale: 'zh-CN' })
  const page = await context.newPage()

  // ---- 1. 在线加载 + SW 注册 ----
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1500)

  const swState = await page.evaluate(async () => {
    if (!('serviceWorker' in navigator)) return { supported: false }
    const reg = await navigator.serviceWorker.getRegistration()
    let ready = null
    try {
      const r = await Promise.race([
        navigator.serviceWorker.ready,
        new Promise((_, rej) => setTimeout(() => rej(new Error('timeout')), 8000)),
      ])
      ready = r ? r.scope : null
    } catch (e) {
      ready = 'timeout'
    }
    return {
      supported: true,
      controller: !!navigator.serviceWorker.controller,
      registration: reg ? { scope: reg.scope, active: !!reg.active, waiting: !!reg.waiting } : null,
      readyScope: ready,
    }
  })
  ok('Service Worker 注册并激活', swState.controller === true && swState.registration?.active === true,
     JSON.stringify(swState))

  // ---- 2. manifest ----
  const manifestInfo = await page.evaluate(async () => {
    const link = document.querySelector('link[rel="manifest"]')
    if (!link) return { link: false }
    try {
      const res = await fetch(link.href)
      const text = await res.text()
      const m = JSON.parse(text)
      return { link: true, fetchable: res.ok, status: res.status, contentType: res.headers.get('content-type'),
               parsed: true, name: m.name, icons: (m.icons || []).map(i => `${i.sizes}${i.purpose ? ':' + i.purpose : ''}`) }
    } catch (e) {
      return { link: true, error: String(e) }
    }
  })
  ok('manifest 可达且可解析', manifestInfo.parsed === true,
     `status=${manifestInfo.status} type=${manifestInfo.contentType} icons=[${(manifestInfo.icons || []).join(',')}]`)
  ok('manifest 图标含 maskable', (manifestInfo.icons || []).some(i => i.includes('maskable')),
     'Android 自适应图标需要 purpose=maskable')

  // ---- 3. 离线整页刷新（核心）----
  await context.setOffline(true)
  let offlineOk = false
  let offlineDetail = ''
  try {
    await page.reload({ waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2000)
    const hasApp = await page.evaluate(() => !!document.querySelector('#app') && document.querySelector('#app').children.length > 0)
    offlineOk = hasApp
    offlineDetail = hasApp ? '离线下 #app 已渲染（SW 兜底成功）' : '离线下 #app 为空'
  } catch (e) {
    offlineDetail = '离线刷新失败: ' + e.message.split('\n')[0]
  }
  ok('离线整页刷新仍有应用壳', offlineOk, offlineDetail)
  await page.screenshot({ path: 'test-results/pwa-offline-home.png' })

  // ---- 4. 离线深链（SPA 路由）----
  let deepOk = false
  let deepDetail = ''
  try {
    await page.goto(BASE + '/dashboard', { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(2000)
    const hasApp = await page.evaluate(() => !!document.querySelector('#app') && document.querySelector('#app').children.length > 0)
    deepOk = hasApp
    deepDetail = hasApp ? '离线深链 /dashboard 可用' : '离线深链 #app 为空'
  } catch (e) {
    deepDetail = '离线深链失败: ' + e.message.split('\n')[0]
  }
  ok('离线深链（SPA 路由）可用', deepOk, deepDetail)

  // ---- 5. 恢复在线，截图登录页（看备案号是否可见）----
  await context.setOffline(false)
  await page.goto(BASE + '/', { waitUntil: 'networkidle' })
  await page.waitForTimeout(1000)
  const beianVisible = await page.evaluate(() => document.body.innerText.includes('ICP备'))
  ok('未登录页（/login）展示备案号', beianVisible, beianVisible ? '' : '匿名首页 footer 无备案号 —— 工信部要求全站底部展示')
  await page.screenshot({ path: 'test-results/pwa-online-login.png', fullPage: true })

  await browser.close()

  const fails = results.filter(r => !r.pass).length
  console.log(`\n${fails === 0 ? '全部通过' : fails + ' 项未通过'}（共 ${results.length} 项）`)
  process.exit(0)
})().catch(e => { console.error('脚本异常:', e); process.exit(1) })
