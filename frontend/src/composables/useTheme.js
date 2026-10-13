import { ref } from 'vue'

/**
 * 主题（墨阶：浅色 / 深色 / 跟随系统）。
 * index.html 里有首帧内联脚本先行打 class，这里只负责状态与切换；
 * 持久化 key 用 'theme'（light | dark | system）。
 */
const theme = ref(localStorage.getItem('theme') || 'system')

const apply = () => {
  const dark =
    theme.value === 'dark' ||
    (theme.value === 'system' && window.matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark)
}

// 系统主题变化时，只有"跟随系统"模式需要联动
window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
  if (theme.value === 'system') apply()
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
