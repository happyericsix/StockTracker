/**
 * 市场状态 → 界面文案（**纯函数，不依赖 Vue、不发请求**）。
 *
 * <h3>为什么单独成一个模块</h3>
 * 这里全是"把一份状态翻译成人话"的判断，而它们最容易错的地方**肉眼看不出来**：
 *
 * - `tradingDay === null` 是"**不知道**今天开不开市"（交易日历拿不到），
 *   不是"休市"。写成 `!status.tradingDay` 会把"不知道"渲染成"今日休市"——
 *   页面看起来完全正常，而它其实在编一个答案。
 * - `phase === 'pre_open'`（交易日开盘前）时，最新价仍然属于**上一个**交易日。
 *   把它当成"今天的价"，就是用户报的那个 bug 的另一半。
 *
 * <p>抽出来之后它可以脱离浏览器直接跑（见文件末尾的用例）——
 * 与 `utils/newsAnchor.js` 同一套理由：这些判断出错的代价高，而靠人眼在
 * 页面上发现它们的概率极低（只有在特定日期、特定时段才会露出来）。
 */

const WEEKDAYS = ['周日', '周一', '周二', '周三', '周四', '周五', '周六']

/**
 * `"2026-09-18"` → `"09-18（周五）"`。
 *
 * <p>星期由**这个日期自己**算出来，不取状态里那个 `weekday` 字段 ——
 * 那个字段说的是"今天"，而这里要格式化的往往是 `quoteDate`（不是今天）。
 * 用错字段的表现是"09-18 被标成周五还是周日"这种没人会细看的错误。
 */
export const formatDay = (value) => {
  const text = String(value || '')
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})$/)
  if (!match) return text
  const [, year, month, day] = match
  // 用 UTC 构造：这里只做星期换算，不需要真实时刻，也就不该受本地时区影响
  const weekday = WEEKDAYS[new Date(Date.UTC(+year, +month - 1, +day)).getUTCDay()]
  return `${month}-${day}（${weekday}）`
}

/**
 * 页面上那些价格**不是今天**的吗？
 *
 * <p>两种情况都成立：① 今天休市（价来自上一个交易日）；
 * ② 今天开市但还没开盘（最新价仍是上一交易日的收盘价）。
 *
 * <p>⚠️ 判据是 `tradingDay === false`，**不是** `!tradingDay` —— 见模块注释。
 */
export const isQuoteFromAnotherDay = (status) => {
  if (!status) return false
  const closed = status.tradingDay === false
  const beforeOpen = status.phase === 'pre_open'
  return (closed || beforeOpen) && !!status.quoteDate
}

/** "行情为 09-18（周五）收盘价，非实时"；不需要提醒时返回空串。 */
export const quoteHintFor = (status) => (
  isQuoteFromAnotherDay(status) ? `行情为 ${formatDay(status.quoteDate)}收盘价，非实时` : ''
)

/**
 * 行情卡右上角那行字。
 *
 * <p>休市日**不能**写"更新 09-18"——那会被读成"数据更新于 09-18"，
 * 而真实含义是"这个价是 09-18 收盘的"。后者才是用户判断"能不能拿它做决策"的依据。
 */
export const quoteDateTextFor = (status, lastUpdated) => {
  if (isQuoteFromAnotherDay(status)) {
    return `最近交易日 ${formatDay(status.quoteDate)}收盘`
  }
  return lastUpdated ? `更新 ${lastUpdated}` : '更新时间未知'
}

/**
 * 图表副标题最前面那段"现在是什么状态"。
 *
 * <p>只在「休市」与**交易日的非连续竞价时段**（未开盘 / 午间休市 / 已收盘）出现。
 * 盘中不给 —— 那时价格本来就是实时的，加一句"交易中"只是噪声。
 * 而"未开盘 / 已收盘"必须说出来：那两段时间里最新价属于上一个时段。
 *
 * @returns {string|null} null 表示"不必说"（含"日历不可用"这种不确定的情况）
 */
export const sessionBadgeFor = (status) => {
  if (!status) return null
  if (status.tradingDay === false) return '今日休市'
  if (status.known === false) return null      // 不知道就别贴标签
  return ['pre_open', 'noon_break', 'post_close'].includes(status.phase)
    ? (status.phaseLabel || null)
    : null
}

/**
 * 状态**拉取失败**（不是"还没到"）时的提示条。
 *
 * <p>与 `buildRestNotice(null)` 的区别很重要：`null` 表示"状态还在路上"（首屏那一瞬），
 * 那时保持沉默是对的。而这里是"问了，但没问到" —— 沉默会让页面**退回**
 * "把休市日的收盘价当成今天"的老行为，那正是本次要修的 bug。所以这一支必须说出来。
 */
export const buildUnavailableNotice = () => ({
  tone: 'unknown',
  badge: '状态未知',
  headline: '暂时拿不到交易日历，无法确认今天是否开市',
  detail: '下方价格可能仍是上一交易日的收盘价，请以交易软件为准',
})

/**
 * 休市提示条的内容。开市、或状态还没到时返回 `null`（不占版面）。
 * 拉取失败请用 {@link buildUnavailableNotice}（那是"问过但没问到"，不能沉默）。
 *
 * <h3>为什么句子本身用后端的 `note` 而不是在这里拼</h3>
 * 后端已经把"连休几天、下一交易日是哪天、还有几天"拼成了一句中文。
 * 前端再拼一遍就是**同一件事的两种说法**：改了后端忘了前端，页面就会出现
 * "连休 2 天"配"下一交易日 3 天后"这种自相矛盾的提示。
 * 所以这里只用结构化字段决定**版式**（徽章说什么、要不要补一句行情口径），
 * 句子本身照搬后端的。
 *
 * @returns {{tone: string, badge: string, headline: string, detail: string}|null}
 */
export const buildRestNotice = (status) => {
  if (!status) return null

  if (!status.known) {
    // 日历不可用：如实说不知道。徽章给警示色 —— 它说的是一件"我们不确定"的事。
    // 注意这里**不返回 null**：沉默会退回"把休市日的收盘价当成今天"的老行为。
    return {
      tone: 'unknown',
      badge: '状态未知',
      headline: status.note || '交易日历暂时不可用，无法确认今天是否开市',
      detail: '',
    }
  }

  if (status.tradingDay !== false) return null

  return {
    tone: 'closed',
    badge: '休市',
    headline: status.note || '今天休市',
    // 这一句是前端**唯一**要自己说的：后端不知道页面上正在显示哪些价格
    detail: quoteHintFor(status),
  }
}

// ==================== 用例（可直接用 Node 跑）====================
//
// 这些判断出错的代价很高，而在浏览器里发现它们的概率极低 ——
// 只有在特定的日期与时段才会露出来。所以把"边界日"当成参数钉死。

const CASES = [
  {
    name: '周日休市：连休几天 + 哪天回来 + 这些价是周五的',
    status: {
      date: '2026-09-20', weekday: '周日', known: true, tradingDay: false,
      phase: 'closed', phaseLabel: '今日休市', quoteDate: '2026-09-18',
      nextTradingDay: '2026-09-21', restDays: 2, restFrom: '2026-09-19', restTo: '2026-09-20',
      note: '今日周末休市（周日） · 本轮连休 2 天（09-19 ~ 09-20） · 下一交易日 09-21（周一，明天）',
    },
    expect: (notice) => {
      if (notice.badge !== '休市') return 'badge 应为「休市」'
      if (!notice.headline.includes('连休 2 天')) return '正文必须说出连休几天'
      if (notice.detail !== '行情为 09-18（周五）收盘价，非实时') return `detail 不对：${notice.detail}`
      if (quoteDateTextFor(CASES[0].status, '2026-09-18') !== '最近交易日 09-18（周五）收盘') {
        return '行情卡的日期口径必须写成「最近交易日 … 收盘」'
      }
      return null
    },
  },
  {
    name: '交易日盘中：不提示、不贴状态标签',
    status: {
      date: '2026-09-21', weekday: '周一', known: true, tradingDay: true,
      phase: 'morning', phaseLabel: '交易中', quoteDate: '2026-09-21', nextTradingDay: '2026-09-22',
      note: '交易中 · 今日 09-21（周一）',
    },
    expect: (notice, status) => {
      if (notice !== null) return '盘中不该占版面'
      if (sessionBadgeFor(status) !== null) return '盘中不该贴"交易中"标签'
      if (quoteHintFor(status) !== '') return '盘中不该说"非实时"'
      return null
    },
  },
  {
    name: '交易日开盘前：开市，但最新价还是上一交易日的',
    status: {
      date: '2026-09-21', weekday: '周一', known: true, tradingDay: true,
      phase: 'pre_open', phaseLabel: '未开盘', quoteDate: '2026-09-18', nextTradingDay: '2026-09-22',
      note: '未开盘 · 今日 09-21（周一）09:30 开盘',
    },
    expect: (notice, status) => {
      if (sessionBadgeFor(status) !== '未开盘') return '开盘前必须说"未开盘"'
      if (!isQuoteFromAnotherDay(status)) return '开盘前的价属于上一个交易日'
      if (quoteDateTextFor(status, '2026-09-18') !== '最近交易日 09-18（周五）收盘') return '日期口径不对'
      return null
    },
  },
  {
    name: '日历不可用（tradingDay=null）：说"不知道"，绝不说"休市"',
    status: {
      date: '2026-09-21', weekday: '周一', known: false, tradingDay: null,
      phase: 'unknown', phaseLabel: '状态未知', quoteDate: '', nextTradingDay: '',
      note: '交易日历暂时不可用，无法确认今天是否开市（行情可能仍是上一交易日的收盘价）',
    },
    expect: (notice, status) => {
      if (notice === null) return '必须显示提示条（沉默会退回"把休市价当成今天"的老行为）'
      if (notice.tone !== 'unknown') return 'tone 必须是 unknown'
      if (notice.badge !== '状态未知') return 'badge 必须是「状态未知」而不是「休市」'
      if (sessionBadgeFor(status) !== null) return '不知道时不该贴"今日休市"'
      if (isQuoteFromAnotherDay(status)) return '不知道时不该断言价来自别的交易日'
      return null
    },
  },
  {
    name: '状态还没到：什么都不说，也不报错',
    status: null,
    expect: (notice, status) => {
      if (notice !== null) return '没状态时不该显示提示条'
      if (sessionBadgeFor(status) !== null) return '没状态时不该贴标签'
      if (quoteDateTextFor(status, null) !== '更新时间未知') return '没状态时的日期口径不对'
      return null
    },
  },
  {
    name: '拉取失败：必须说出来，不能沉默退回老行为',
    status: 'UNAVAILABLE',
    expect: (notice) => {
      if (notice === null) return '拉取失败时不能沉默'
      if (notice.tone !== 'unknown') return 'tone 必须是 unknown'
      // 与"日历不可用"同一档：都说"不知道"，不猜"休市"也不猜"开市"
      const unavailable = buildUnavailableNotice()
      if (notice.headline !== unavailable.headline) return '应使用拉取失败那句文案'
      if (!notice.detail.includes('上一交易日')) return '必须提醒价格口径可能不对'
      return null
    },
  },
  {
    name: 'formatDay 兜住脏值（后端给了空串/N/A 时不能渲染成 NaN）',
    status: null,
    expect: () => {
      if (formatDay('') !== '') return 'formatDay("") 应为空串'
      if (formatDay(null) !== '') return 'formatDay(null) 应为空串'
      if (formatDay('N/A') !== 'N/A') return 'formatDay 应原样返回无法解析的值'
      if (formatDay('2026-01-04') !== '01-04（周日）') return `星期算错了：${formatDay('2026-01-04')}`
      return null
    },
  },
]

/**
 * 跑一遍上面那些用例。返回失败清单（空数组 = 全过）。
 * 导出是为了让它在需要时能被自动跑起来，而不必依赖浏览器。
 */
export const verifyMarketStatusWording = () => {
  const failures = []
  for (const item of CASES) {
    // 哨兵：这一支测的是"请求失败"，不是任何一份真实状态
    const notice = item.status === 'UNAVAILABLE' ? buildUnavailableNotice() : buildRestNotice(item.status)
    const problem = item.expect(notice, item.status)
    if (problem) failures.push(`${item.name} → ${problem}`)
  }
  return failures
}
