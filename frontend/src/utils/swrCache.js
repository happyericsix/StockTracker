/**
 * 页面级 SWR 快照（2026-10-24）：进页面先用本地缓存秒出旧数据，
 * 页面自己的网络请求回来后由 Vue 响应式原地换新——"旧数据一跳变成新数据"，
 * 全程无手动刷新。
 *
 * <h3>与 Service Worker runtimeCaching 的分工（为什么不直接改 SW 配置）</h3>
 * - SW 的 market-api-cache 管的是"离线时还有东西可看"（NetworkFirst 回退）；
 * - 若把 SW 改成 StaleWhileRevalidate，axios 的请求会**先拿到旧响应**（后台才更新
 *   SW 缓存），页面要等第二次加载才能看到新数据——恰好破坏"一跳换新"。
 * - 所以缓存放页面层：首帧读快照，revalidate 仍每次真实打网络（口径不变：
 *   该 NetworkOnly 的接口照旧每次打网络，这里根本不给它们接快照）。
 *
 * <h3>哪些数据可以接</h3>
 * 只读市场数据（K线/报价/估值/资金/情绪/市场状态）与**按账号分键**的自选列表。
 * 资讯（/news）按 vite.config.js 定下的口径永远不缓存；其它用户态数据不接。
 *
 * 所有读写都包 try/catch：隐私模式/存储满/JSON 损坏一律当"没有缓存"，
 * 缓存层绝不能把页面搞挂。
 */

const PREFIX = 'swr:'
/** 默认一天：多数接入方显式传自己的 TTL，这个值只兜底 */
const MAX_AGE_DEFAULT = 24 * 3600 * 1000
/** 单条快照序列化上限：日线全量 1000 根约 100~200KB，1MB 是 5 倍余量 */
const MAX_SIZE = 1024 * 1024

/**
 * 读快照。过期/缺失/损坏都返回 null（调用方按"没有缓存"走原逻辑）。
 * @param {string} key 业务键（不含前缀）
 * @param {number} maxAgeMs 快照最大年龄，超过视为不存在
 * @returns {*} 快照值，或 null
 */
export const swrRead = (key, maxAgeMs = MAX_AGE_DEFAULT) => {
  try {
    const raw = localStorage.getItem(PREFIX + key)
    if (!raw) return null
    const snap = JSON.parse(raw)
    if (!snap || typeof snap !== 'object' || !('v' in snap)) return null
    if (Date.now() - (Number(snap.at) || 0) > maxAgeMs) return null
    return snap.v
  } catch {
    return null
  }
}

/** 写快照（带写入时刻）。失败静默：下次成功获取再写。 */
export const swrWrite = (key, value) => {
  try {
    const raw = JSON.stringify({ v: value, at: Date.now() })
    if (raw.length > MAX_SIZE) return
    localStorage.setItem(PREFIX + key, raw)
  } catch {
    /* 存储满/隐私模式：跳过 */
  }
}

/**
 * 用户态数据的缓存键：带上账号（JWT 的 sub）。
 * 同一台设备换账号登录时读不到上一账号的键——vite.config.js 注释里
 * "NetworkFirst 会在换账号后回放上一账号缓存"的串台风险，在这里从键上杜绝。
 */
export const accountKey = (key) => {
  let sub = 'anon'
  try {
    const token = localStorage.getItem('token')
    if (token) {
      const bin = atob(token.split('.')[1].replace(/-/g, '+').replace(/_/g, '/'))
      // sub 可能是中文：atob 按字节出字符串，先转 %xx 再 decodeURIComponent 才不乱码
      const json = decodeURIComponent(Array.from(bin,
        (c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0')).join(''))
      sub = JSON.parse(json).sub || 'anon'
    }
  } catch {
    /* 坏 token / 无 token：按未登录处理，键退化为 anon 命名空间 */
  }
  return `u:${sub}:${key}`
}
