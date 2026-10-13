/**
 * 事件日期 → 图表 x 轴上的交易日（个股页事件锚定）。
 *
 * <h3>为什么要单独成一个模块</h3>
 * 这是 N5b 里最容易错、又最难靠肉眼发现的一段：三条规则都来自实测的坑，而错了的
 * 表现是"标记出现在不该出现的那一天"——页面看起来完全正常。
 * 抽出来之后它可以用 Node 直接跑验证（见文件末尾的用例），不必依赖浏览器。
 *
 * <h3>三条规则（全部来自实测，不是假想）</h3>
 * 1. **公告只给纯日期**（`公告日期` 是 `"2026-09-19"`，时间是 `00:00:00`），而 x 轴是
 *    交易日序列。拿一个轴上不存在的值去设 `markLine.xAxis`，ECharts 会**静默不渲染**
 *    （不报错），于是"标记偶尔消失"这种问题极难定位。
 * 2. **落在非交易日的公告影响的是下一个交易日**（周六的公告，周五的盘早收完了）。
 *    ⚠️ 这里不能靠"时间 ≥ 15:00 就是盘后"来判断：公告的 time 部分是 `00:00`，
 *    那条判据永远不成立，于是周六公告会被错误地向前吸到周五。
 * 3. **盘后公告（15:00 及以后）影响次日**，不是当日。
 *
 * 另外：**早于可视窗口的事件不打标记**。否则它会被吸到窗口第一根 K 线上，
 * 看起来像"那天出的消息"，而其实是更早的事（它仍然留在下方列表里）。
 */

/** `"2026-09-19T10:11:51"` / `"2026-09-19"` / `"  2026-09-19  "` → `"2026-09-19"` */
export const dayOf = (value) => String(value ?? '').trim().slice(0, 10)

/** ISO 形态的**日期**前缀。用于挡住 `"nan"` / `"未知"` 这类脏值。 */
const DATE_SHAPE = /^\d{4}-\d{2}-\d{2}$/

/**
 * @param {string} publishedAt 事件的发布时间（后端 `publishedAt`，ISO 形态或纯日期）
 * @param {string[]} tradingDays 图表当前绘制用的交易日序列（升序，切片之后）
 * @returns {string} 轴上的交易日；无法锚定（无时间 / 脏值 / 早于窗口 / 无交易日）时返回空串
 */
export const anchorDateFor = (publishedAt, tradingDays) => {
  const dates = Array.isArray(tradingDays) ? tradingDays : []
  if (!dates.length) return ''
  const raw = dayOf(publishedAt)
  // ⚠️ 必须**校验形状**，不能只判非空：下面的比较全是字符串比较，而 `"nan" > "2026-09-18"`
  // 是成立的（'n' > '2'），于是一个脏时间戳会被锚到窗口最后一天 —— 看起来完全正常。
  // 实际上 Python 的 parse_datetime 与 Java 的 parsePublishedAt 都会把 nan 转成空值，
  // 但它到不了前端这件事是**运气而不是保证**（多一条上游路径就多一次机会）。
  if (!DATE_SHAPE.test(raw)) return ''
  if (raw < dates[0]) return ''          // 早于可视窗口：不标记
  const last = dates[dates.length - 1]
  if (raw > last) return last            // 晚于窗口（例如最新公告的次日还没开盘）
  const nextTradingDay = () => dates.find(day => day > raw) || last

  if (!dates.includes(raw)) return nextTradingDay()   // 规则 2：非交易日 → 下一个交易日
  const timePart = String(publishedAt || '').slice(11, 16)
  if (timePart && timePart >= '15:00') return nextTradingDay()   // 规则 3：盘后 → 次日
  return raw
}
