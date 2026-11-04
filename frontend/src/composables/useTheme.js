import { ref } from 'vue'

/**
 * 主题（墨阶：浅色 / 深色 / 跟随系统）。
 * index.html 里有首帧内联脚本先行打 class，这里只负责状态与切换；
 * 持久化 key 用 'theme'（light | dark | system）。
 */
const theme = ref(localStorage.getItem('theme') || 'system')

// 状态栏/窗口标题栏色随主题翻转（2026-10-29）：此前 index.html 写死 #13151c，
// 装到桌面的 PWA 切到浅色后窗口框仍是墨色，观感是"没切干净"。
// 值与 DESIGN.md 的 --bg 保持同源：深色用侧栏墨色（状态栏与壳连成一片），
// 浅色用页面底色。
const THEME_COLOR = { dark: '#13151c', light: '#f5f6f8' }

const apply = () => {
  const dark =
    theme.value === 'dark' ||
    (theme.value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) meta.setAttribute('content', dark ? THEME_COLOR.dark : THEME_COLOR.light)
}

// 系统主题变化时，只有"跟随系统"模式需要联动
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (theme.value === 'system') apply()
})

// 多窗口同步（2026-10-29）：桌面 PWA 窗口与浏览器标签同时开着时，
// 一处切主题另一处原地跟换，不再各切各的
window.addEventListener('storage', (e) => {
  if (e.key !== 'theme') return
  theme.value = e.newValue || 'system'
  apply()
})

export function useTheme() {
  const setTheme = (value) => {
    theme.value = value
    if (value === 'system') localStorage.removeItem('theme')
    else localStorage.setItem('theme', value)
    apply()
  }
  const toggle = () => setTheme(document.documentElement.classList.contains('dark') ? 'light' : 'dark')
  return { theme, setTheme, toggle }
}
