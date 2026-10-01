// 市场状态文案的边界用例 —— 从 src/utils/marketStatus.js 搬出（2026-09-21）。
// 这些判断出错的代价很高，而在浏览器里发现它们的概率极低（只有特定日期与时段才会露出来），
// 所以把"边界日"当成参数钉死。跑法：node tests/marketStatus.cases.mjs
import {
  buildRestNotice,
  buildUnavailableNotice,
  formatDay,
  isQuoteFromAnotherDay,
  quoteDateTextFor,
  quoteHintFor,
  sessionBadgeFor,
} from '../src/utils/marketStatus.js'

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

/** 跑一遍上面那些用例。返回失败清单（空数组 = 全过）。 */
const verifyMarketStatusWording = () => {
  const failures = []
  for (const item of CASES) {
    // 哨兵：这一支测的是"请求失败"，不是任何一份真实状态
    const notice = item.status === 'UNAVAILABLE' ? buildUnavailableNotice() : buildRestNotice(item.status)
    const problem = item.expect(notice, item.status)
    if (problem) failures.push(`${item.name} → ${problem}`)
  }
  return failures
}

const failures = verifyMarketStatusWording()
if (failures.length) {
  console.error('✗ 市场状态文案用例失败：')
  for (const f of failures) console.error('  - ' + f)
  process.exit(1)
}
console.log('✓ 市场状态文案用例全部通过')
