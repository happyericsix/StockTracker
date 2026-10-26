import { createApp } from 'vue'
import App from './App.vue'
import './style.css'
import router from './router/index.js'

createApp(App).use(router).mount('#app')

// PWA 注册、接管触发与版本比对更新（vite.config.js 已设 injectRegister:false，注册只在这一处）。
//
// —— controllerchange → 空闲刷新（2026-10-26 重订）——
// 旧策略「新 SW 接管即 location.reload()」会在使用中途打断用户：整页重初始化 +
// 所有页面数据重新加载，实测像"切主题就卡顿、数据加载失败"（2026-10-26 排查
// 确认与主题切换无关，元凶就是这次刷新撞上交互）。新策略：接管只说明新代码
// **就绪**，旧版本可以继续用；刷新推迟到「90 秒无交互或页面已切后台」，或用户
// 点提示条上的「立即刷新」。资产本身是内容哈希文件名，SW 预缓存更新只下载
// 有变化的文件——文件级增量早已成立，这里补上的是"不打断"。
//
// —— 版本比对自动更新（2026-10-25 引入，2026-10-26 降频）——
// 浏览器只在**导航时**检查 sw.js，挂了几天不导航的老窗口（PWA 常态）永远等不到
// 更新——"用户一直停在旧版"的真正来源。运行中的应用自己拉 /version.json 与当前
// 构建号比对，但 2 分钟一次太吵（频繁比对 + 更新窗口抢占带宽），降为 **12 小时
// 至多一次**（localStorage 记上次检查时间；隐私模式存不了则退化为定时器到期即查）：
//   不一致 → 提示条 + 主动 registration.update() → SW 后台装好接管 → 空闲刷新。

// —— 更新提示条（原生 DOM：注册可能发生在 Vue 挂载之后，不耦合组件）——
let toastEl = null
const showUpdateToast = (text, action) => {
  if (!toastEl) {
    toastEl = document.createElement('div')
    toastEl.setAttribute('role', 'status')
    Object.assign(toastEl.style, {
      position: 'fixed', bottom: '20px', left: '50%', transform: 'translateX(-50%)',
      zIndex: '80', maxWidth: 'calc(100% - 32px)',
      padding: '10px 16px', borderRadius: '12px', fontSize: '12.5px', lineHeight: '1.5',
      color: 'var(--ink, #1b1e26)', background: 'var(--surface, #fff)',
      border: '1px solid var(--border, #e3e6ec)',
      boxShadow: '0 8px 24px rgba(0,0,0,.14)', whiteSpace: 'nowrap',
      display: 'flex', alignItems: 'center', gap: '10px',
    })
    document.body.appendChild(toastEl)
  }
  // textContent 赋值会清掉旧按钮：同一条提示条从「更新中」换「已就绪」时按钮自然换代
  toastEl.textContent = text
  if (action) {
    const btn = document.createElement('button')
    btn.textContent = action.label
    Object.assign(btn.style, {
      border: 'none', borderRadius: '8px', padding: '6px 12px', fontSize: '12.5px',
      background: 'var(--accent, #2153d6)', color: 'var(--on-accent, #fff)',
      cursor: 'pointer', whiteSpace: 'nowrap', flexShrink: '0',
    })
    btn.addEventListener('click', action.onClick)
    toastEl.appendChild(btn)
  }
}
const hideUpdateToast = () => { toastEl?.remove(); toastEl = null }

const RUNNING_VERSION = __APP_VERSION__
let swRegistration = null
let mismatchHandled = false
let reloadWhenIdleTimer = null
let lastInteractionAt = Date.now()

// 新 SW 装好后停在 waiting 等**页面发消息**才接管：GenerateSW 的 sw.js 没有
// 顶层 skipWaiting()，只注册了 {type:'SKIP_WAITING'} 消息钩子（见 dist/sw.js）。
// 2026-10-16 的 controllerchange→reload 只接了后半段（接管后刷新），前半段
// （谁来触发接管）一直缺着——新 SW 装好→没人喊它上岗→页面永不刷新，
// 这才是"PWA 永远停在旧版"的真正根因（2026-10-25 复盘确认）。
const activateWaitingWorker = () => {
  swRegistration?.waiting?.postMessage({ type: 'SKIP_WAITING' })
}

// —— 就绪后的空闲刷新 ——
// 「空闲」= 90 秒无任何交互，或页面已切后台（后台刷新无感，回到前台即是新版）。
// 用户持续交互不停手时保持旧版本继续服务——这正是本策略的用意；提示条上的
// 「立即刷新」按钮给等不及的人。交互打点用捕获阶段的 passive 监听，开销为零。
;['pointerdown', 'keydown', 'touchstart', 'wheel'].forEach((evt) =>
  document.addEventListener(evt, () => { lastInteractionAt = Date.now() }, { passive: true }))

const onNewVersionReady = () => {
  showUpdateToast('新版本已就绪，空闲时自动启用', { label: '立即刷新', onClick: () => location.reload() })
  if (!reloadWhenIdleTimer) {
    reloadWhenIdleTimer = setInterval(() => {
      if (document.hidden || Date.now() - lastInteractionAt > 90_000) location.reload()
    }, 20_000)
  }
}

const fetchServerVersion = async () => {
  try {
    const res = await fetch('/version.json', { cache: 'no-store' })
    if (!res.ok) return null
    return (await res.json())?.version || null
  } catch {
    return null   // 断网/未部署 version.json（本地 dev）：静默，下次再比
  }
}

const checkVersion = async () => {
  if (mismatchHandled) return
  const server = await fetchServerVersion()
  if (!server || server === RUNNING_VERSION) return
  mismatchHandled = true
  showUpdateToast('发现新版本，正在后台更新，不影响当前使用…')
  swRegistration?.update().catch(() => {})
  activateWaitingWorker()   // 上一会话可能已留下装好的 waiting worker，直接喊它上岗
}

// 12 小时至多拉一次 version.json（2026-10-26）：localStorage 记上次检查时间。
// 存不了（隐私模式等）就退化为「定时器到期即查」——interval 本身已是半小时级。
const VERSION_CHECK_MIN_GAP = 12 * 60 * 60 * 1000
const VERSION_CHECK_TS_KEY = 'pwa-last-version-check'
const maybeCheckVersion = () => {
  try {
    const last = +(localStorage.getItem(VERSION_CHECK_TS_KEY) || 0)
    if (Date.now() - last < VERSION_CHECK_MIN_GAP) return
    localStorage.setItem(VERSION_CHECK_TS_KEY, String(Date.now()))
  } catch { /* 存不了就照查 */ }
  checkVersion()
}

// 版本比对不依赖 SW：即使注册失败（隐私模式等），低频轮询仍然工作
setInterval(maybeCheckVersion, 30 * 60 * 1000)
// 回前台只做版本比对，**不**无条件 registration.update()（2026-10-26）：
// update() 每次都绕过 HTTP 缓存重拉 sw.js——用户反复切换 PWA 前后台时，
// 这里会连发一串网络请求叠在页面的数据加载上（移动网络下体感就是"切回来卡"）。
// 版本真变了时 checkVersion 内部自会调 update()，这里不需要抢先。
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') maybeCheckVersion()
})

if ('serviceWorker' in navigator) {
  window.addEventListener('load', async () => {
    try {
      const registration = await navigator.serviceWorker.register('/sw.js', { scope: '/' })
      swRegistration = registration

      // 上个会话可能已经留下一个装好但没人喊的 waiting worker：立刻补一嗓子
      activateWaitingWorker()

      // 新 SW 开始安装（= 正在下载新预缓存；文件级差量——内容哈希文件名
      // 没变的不会重下）。installed → 发 SKIP_WAITING 让它接管 →
      // controllerchange → 空闲刷新；redundant（安装失败/被替代）→ 收掉提示。
      registration.addEventListener('updatefound', () => {
        const worker = registration.installing
        if (!worker) return
        // 首次注册（页面此前没有 controller）同样触发 updatefound：那不是"更新"，
        // 别弹"正在后台更新"——它没有对应的 controllerchange，会永远挂在页面上
        if (!navigator.serviceWorker.controller) return
        showUpdateToast('正在后台更新新版本，不影响当前使用…')
        worker.addEventListener('statechange', () => {
          if (worker.state === 'installed') activateWaitingWorker()
          if (worker.state === 'redundant') hideUpdateToast()
        })
      })

      // 新 SW 接管：旧代码继续跑，等空闲（或用户点按钮）再换新——不再立即刷新
      navigator.serviceWorker.addEventListener('controllerchange', onNewVersionReady)

      maybeCheckVersion()
    } catch {
      // SW 注册失败（隐私模式/策略限制）不影响应用本身，低频版本轮询在上面继续跑
    }
  })
}
