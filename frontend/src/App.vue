<script setup>
import { ref, onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { CandlestickChart, X } from 'lucide-vue-next'
import Button from './components/ui/button.vue'

/**
 * PWA 安装提示：五档降级。
 *
 * - Android Chrome 等：原生 beforeinstallprompt → 自定义横幅调 prompt()。
 * - iOS（Safari/微信/所有 WebView）：**没有** beforeinstallprompt，永远拿不到
 *   原生弹窗——只能引导用户手动「分享 → 添加到主屏幕」。
 * - 微信内（安卓/iOS 都算）：WebView 不支持安装，先引导「浏览器打开」。
 * - Android 其它浏览器（QQ/UC/夸克/厂商自带）：不触发 beforeinstallprompt，
 *   之前直接返回 null 什么都不弹——用户"手机上从没见过安装提示"多发生在这里
 *   （含 Chrome 曾拒绝过原生条被静默的情况）。引导走浏览器菜单手动添加。
 * - 桌面第三方浏览器（联想/QQ 等 Chromium 壳）：同样没有原生事件（2026-10-25
 *   实测用户就用联想浏览器，桌面端此前永远无提示），给菜单指引；
 *   Chrome/Edge 原生事件到达后自动切换成可点安装。
 * 已安装（standalone）不弹；用户关闭后 7 天内不再打扰。
 */
const DISMISS_KEY = 'pwa-install-dismissed-at'
const DISMISS_TTL = 7 * 24 * 3600 * 1000

const installEvent = ref(null)
// null=不显示；'install'=原生可装；'ios'=iOS 手动引导；'wechat'=先去浏览器；
// 'android'=安卓无原生事件的浏览器手动引导
const banner = ref(null)
let fallbackTimer = null

const isStandalone = () =>
  matchMedia('(display-mode: standalone)').matches || navigator.standalone === true

// iPadOS 13+ 的 Safari 伪装成 Mac，靠触点数识别
const isIOS = () =>
  /iphone|ipad|ipod/i.test(navigator.userAgent) ||
  (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)

const isWeChat = () => /MicroMessenger/i.test(navigator.userAgent)

const isAndroid = () => /android/i.test(navigator.userAgent)

const recentlyDismissed = () => {
  try {
    const t = +(localStorage.getItem(DISMISS_KEY) || 0)
    return !!t && Date.now() - t < DISMISS_TTL
  } catch { return false }
}

// 触屏设备判定（手机/平板）：其余视为桌面
const isMobileLike = () =>
  typeof navigator !== 'undefined' &&
  (navigator.maxTouchPoints > 1 && /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent))

const decideBanner = () => {
  if (isStandalone() || recentlyDismissed()) return null
  if (installEvent.value) return 'install'
  if (isWeChat()) return 'wechat'
  if (isIOS()) return 'ios'
  if (isAndroid()) return 'android'
  // 桌面端没触发原生事件的浏览器（联想/QQ 等第三方 Chromium 壳）也给菜单指引；
  // Chrome/Edge 的原生事件来了会自动切成可点安装（onBip）
  if (isMobileLike()) return null
  return 'desktop'
}

const bannerText = {
  install: { title: '安装 Stock Tracker', sub: '添加到桌面，快速查看行情' },
  ios: { title: '安装到主屏幕', sub: '点 Safari 底部分享按钮，选「添加到主屏幕」' },
  wechat: { title: '在浏览器中打开即可安装', sub: '点右上角「···」→「在浏览器打开」，再添加到桌面' },
  android: { title: '安装到主屏幕', sub: '点浏览器菜单（⋮ 或「三」），选「添加到主屏幕」或「安装应用」' },
  desktop: { title: '安装到桌面', sub: '点浏览器菜单，选「安装应用」或「添加到桌面」，可像客户端一样使用' },
}

const onBip = (e) => {
  e.preventDefault()
  installEvent.value = e
  // 原生事件来了就直接换上（若手动引导已经亮着也换成可点的安装）
  if (!isStandalone() && !recentlyDismissed()) banner.value = 'install'
}

const onInstalled = () => {
  banner.value = null
  installEvent.value = null
}

const dismiss = () => {
  banner.value = null
  try { localStorage.setItem(DISMISS_KEY, String(Date.now())) } catch {}
}

const handleInstall = async () => {
  if (!installEvent.value) { dismiss(); return }
  installEvent.value.prompt()
  const result = await installEvent.value.userChoice
  // 原生弹窗里点了拒绝也视为「近期不想装」，7 天内不再弹
  if (result.outcome !== 'accepted') dismiss()
  else banner.value = null
  installEvent.value = null
}

onMounted(() => {
  window.addEventListener('beforeinstallprompt', onBip)
  window.addEventListener('appinstalled', onInstalled)
  // 兜底展示：原生事件不来（iOS/微信/不支持安装的浏览器）时，
  // 稍等几秒再给手动引导——既给 beforeinstallprompt 留触发窗口，也不抢首屏
  fallbackTimer = setTimeout(() => { banner.value = decideBanner() }, 4000)
})
</script>

<template>
  <RouterView />

  <!-- PWA 安装横幅：浮层卡片（唯一带阴影的层级之一） -->
  <div
    v-if="banner"
    class="fixed bottom-[calc(20px+env(safe-area-inset-bottom,0px))] left-1/2 z-50 flex w-[min(420px,calc(100%-32px))] -translate-x-1/2 items-center gap-3 rounded-xl border border-border bg-surface p-3.5"
    style="box-shadow: var(--shadow-pop)"
    role="dialog"
    aria-label="安装提示"
  >
    <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
      <CandlestickChart :size="22" :stroke-width="1.75" aria-hidden="true" />
    </span>
    <div class="min-w-0 flex-1">
      <p class="text-[13px] font-medium text-ink">{{ bannerText[banner].title }}</p>
      <p class="text-[12px] text-ink-2">{{ bannerText[banner].sub }}</p>
    </div>
    <div class="flex shrink-0 items-center gap-1">
      <Button v-if="banner === 'install'" size="sm" variant="primary" @click="handleInstall">安装</Button>
      <Button v-else size="sm" variant="primary" @click="dismiss">我知道了</Button>
      <Button size="icon-sm" variant="ghost" aria-label="关闭安装提示" @click="dismiss">
        <X :size="15" :stroke-width="2" aria-hidden="true" />
      </Button>
    </div>
  </div>
</template>
