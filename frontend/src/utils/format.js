/**
 * 涨跌方向与数字格式化（纯函数，多页共用）。
 *
 * 两条纪律（自旧版各页原样集中，语义零漂移）：
 *  1. 取不到就返回 null/中性，**不猜方向**——把没有方向画成红或绿，
 *     等于编了一个不存在的方向。
 *  2. 方向不能只靠颜色：显示带符号数值（+12.34 / -5.60），颜色只是强化。
 */

/** 脏值（'-'、'--'、'N/A'、空串、非数字）统一归 null */
export const toNum = (v) => {
  if (v === null || v === undefined) return null
  const s = String(v).trim()
  if (s === '' || s === '-' || s === '--' || s === 'N/A') return null
  const n = Number(s)
  return Number.isFinite(n) ? n : null
}

/** 'up' | 'down' | 'flat' | null（null=无数据，调用方自行给中性） */
export const dirOf = (v) => {
  const n = toNum(v)
  if (n === null) return null
  return n > 0 ? 'up' : n < 0 ? 'down' : 'flat'
}

/** 数字文字色（红涨绿跌；flat 与无数据走中性） */
export const dirTextClass = (v) => {
  const d = dirOf(v)
  return d === 'up' ? 'text-up' : d === 'down' ? 'text-down' : 'text-ink-2'
}

/** 徽章 tone（配 ui/badge.vue） */
export const dirTone = (v) => dirOf(v) ?? 'flat'

/** 带符号格式化；四舍五入到 0 的按平盘显示，避免出现 "-0.00" */
export const signed = (v, suffix = '') => {
  const n = toNum(v)
  if (n === null) return ''
  const r = Math.round(n * 100) / 100
  return (r > 0 ? '+' : '') + r.toFixed(2) + suffix
}

/** ISO 时间 → "2026-10-13 09:30:00"（无值给空串） */
export const dateTime = (v) => {
  const s = String(v || '')
  return s ? s.replace('T', ' ').slice(0, 19) : ''
}
