import { ref } from 'vue'

/**
 * 壳层与根组件共享的瞬时 UI 状态。
 *
 * 由来（2026-10-29）：App.vue 的安装横幅固定在视口底部 z-50，抽屉导航 z-40 在其
 * 之下——手机上抽屉底部一行（市场状态/用户/主题切换/退出登录）正好被横幅盖住，
 * 点击全部被拦截（Playwright 实测 "intercepts pointer events"）。抽屉开着时横幅
 * 让路，通过这个共享 ref 让 App.vue（RouterView 之外）感知抽屉状态。
 */
export const drawerOpen = ref(false)
