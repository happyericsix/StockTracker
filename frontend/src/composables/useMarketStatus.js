import { ref, computed } from 'vue'
import { getMarketStatus } from '../api/market.js'
import {
  buildRestNotice,
  formatDay,
  isQuoteFromAnotherDay,
  quoteDateTextFor,
  sessionBadgeFor,
} from '../utils/marketStatus.js'

/**
 * 市场状态（休市提示）的唯一入口。
 *
 * <h3>为什么是一个模块级单例，而不是每个页面各拉一次</h3>
 * 首页行情卡与个股 K 线页都要用它，而它们可能在同一分钟内被来回切换。
 * 每次进页面都重新问一次后端，等于给"点开一只票"这件事多加一次往返 ——
 * 本功能本来就是为了让页面**更快说清楚状况**，自己先慢下来就说不过去了。
 * 后端的缓存 TTL 是 60 秒（盘中阶段要随分钟变化），这里对齐同一个数量级。
 *
 * <h3>为什么不用 store（Pinia）</h3>
 * 它没有需要被 DevTools 追踪的状态，也不需要跨页面持久化；
 * 一个模块级 ref 就够了，而且不引入任何依赖。
 *
 * <p>「状态 → 文案」的判断全在 `utils/marketStatus.js`（纯函数、可脱离浏览器跑）。
 * 这里只负责取数、缓存与把结果包成 computed。
 */

/** 与后端 MarketStatusService 的缓存 TTL 对齐：超过它就该重新问一次 */
const TTL_MS = 60_000

const status = ref(null)
const loading = ref(false)
/** 最近一次拉取失败。**不清空 status** —— 上一份状态（哪怕几分钟前的）也比"什么都不说"有用 */
const failed = ref(false)
let loadedAt = 0

export function useMarketStatus() {
  /**
   * 拉一次市场状态。
   *
   * @param {boolean} force 忽略 TTL 强制刷新（用户手动重试时用）
   */
  const load = async (force = false) => {
    if (!force && status.value && Date.now() - loadedAt < TTL_MS) return status.value
    // 并发去重：同一时刻多个组件挂载时只发一次（`loading` 期间直接返回现状）
    if (loading.value) return status.value
    loading.value = true
    try {
      const res = await getMarketStatus()
      const data = res?.data
      if (data && typeof data === 'object') {
        status.value = data
        loadedAt = Date.now()
        failed.value = false
      }
    } catch (e) {
      // 拿不到就保留上一份（可能没有）。**不把失败当成"今天开市"** ——
      // 那正是这次要修的 bug：宁可不显示，也不要给出一个错的确定答案。
      failed.value = true
    } finally {
      loading.value = false
    }
    return status.value
  }

  /**
   * 「下面这些价不是今天的」—— 这一刻最该说清楚的事。
   * 判据（含 `tradingDay === null` 那个坑）在 `utils/marketStatus.js`，那里有用例钉着。
   */
  const quoteIsFromAnotherDay = computed(() => isQuoteFromAnotherDay(status.value))

  /** `"2026-09-18"` → `"09-18（周五）"` */
  const quoteDateLabel = computed(() => formatDay(status.value?.quoteDate))

  /**
   * 提示条。三分支，顺序有意义：
   * ① 有状态 → 按状态给（开市时是 null，不占版面）；
   * ② 没状态但**拉取失败过** → 必须说出来（沉默会退回"把休市价当成今天"的老行为）；
   * ③ 没状态也没失败 → 还在路上，保持沉默。
   */
  const restNotice = computed(
    () => buildRestNotice(status.value) ?? (failed.value ? buildUnavailableNotice() : null))

  return {
    status,
    failed,
    load,
    quoteIsFromAnotherDay,
    quoteDateLabel,
    restNotice,
    /** 图表副标题的"现在是什么状态"前缀（未开盘 / 午间休市 / 已收盘 / 今日休市） */
    sessionBadge: computed(() => sessionBadgeFor(status.value)),
    /**
     * 行情卡右上角那行字。
     *
     * 包成一个方法（而不是 computed）是因为它还要吃一个参数（行情源给的时间戳），
     * 而那个参数来自调用方自己的状态 —— 放在这里组合会让这个 composable
     * 反过来依赖每个页面的字段名。
     */
    quoteDateTextFor: (lastUpdated) => quoteDateTextFor(status.value, lastUpdated),
  }
}
