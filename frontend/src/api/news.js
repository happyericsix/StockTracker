import request from './request.js'

/**
 * 个股事件时间轴（公告/媒体/研报，按发布时间倒序）+ **后台解读的进度**。
 *
 * 后端对应 `NewsController` 的 `GET /api/v1/news/events?symbol=&days=`，
 * 返回 `Result<StockNewsTimelineResponse>` —— `request.js` 的响应拦截器已经把
 * `Result` 信封剥掉，所以 `res.data` 是 `{events, analyzing, analyzedCount, total, pendingCount}`。
 *
 * <h3>2026-09-20：从"裸数组"改成"带进度的信封"</h3>
 * 这条接口以前会在返回**之前**同步做完"兜底抓取 + 最多 6 次批量 LLM 解读"，
 * 用户点进来先空等几秒、一个字的正文都没有。现在它只读库并立刻返回，
 * 抓取与解读交给后端后台，进度靠上面这三个字段告诉前端：
 *
 * - `analyzing`  是否**仍有**在途任务 → 决定要不要继续轮询（false 就该停，否则是一个永远转下去的圈）
 * - `analyzedCount` / `total` → 显示"已解读 x/y"，让等待有形状
 * - `pendingCount` → 这一轮预计会补多少条
 *
 * ⚠️ 仍然可能偏慢的只有**第一次**：库里没有这只票的数据时要等后端抓上游。
 * 所以调用方一律软失败（`.catch`），不要让它挡住 K 线的渲染。
 */
export const getStockEvents = (symbol, days = 90) =>
  request.get('/news/events', { params: { symbol, days } })

/**
 * 个股「当前怎么看」：后端读完这批事件之后给出的一段连贯判断。
 *
 * 与逐条解读的分工：事件流给每条一个方向标签，这个给**综合**结论 ——
 * 用户的原话是"你要去阅读实时的新闻去更新你的想法，而不是一个新闻一个想法"。
 *
 * ⚠️ 拿不到时后端返回 `data = null`（不是错误）：**"这次给不出结论"是合法状态**，
 * 调用方应当隐藏整块，而不是显示一段空话。
 */
export const getStockRead = (symbol, days = 90) =>
  request.get('/news/stock-read', { params: { symbol, days } })

/**
 * 预取一只票的资讯时间轴（**故意吞掉所有错误**）。
 *
 * <h3>为什么值得单独写一个函数</h3>
 * 首屏慢的那几秒里，一半是"后端要临时去抓上游"。而用户点进个股页之前
 * 一定先在首页的自选/搜索里**看过这只票**——那个时刻正是可以提前把抓取与解读
 * 挂上去的时候。等真正跳转过去时，库已经是热的。
 *
 * <p>用 `GET /news/events` 而不是 `stock-read`：后者要多花一次综合解读的模型调用，
 * 而用户可能只是划过、并不会点进去。前者会顺带把"抓 + 补解读"发到后台
 * （受每标的 5 分钟冷却约束，重复调用不会重复花钱）。
 *
 * <p>刻意不返回 Promise：调用方在跳转之前不应该等它。
 */
export const prefetchStockNews = (symbol, days = 90) => {
  if (!symbol) return
  getStockEvents(symbol, days).catch(() => {
    // 预取失败完全无所谓：真正进页面时会照常再拉一次
  })
}

/**
 * 全市场资讯搜索（N4 搜索页）。
 *
 * 后端 `POST /api/v1/news/search`，body 形状见 `NewsSearchRequest`：
 * {keyword?, symbol?, types?, days=7, scope?, page=0, size=20}。
 * 返回 `Result<Page<NewsEventResponse>>` —— 拦截器剥掉 Result 后
 * `res.data` 是 Spring Data 的 Page：`.content / .totalPages / .totalElements`。
 * 响应里每条带 credibility/credibilityGrade/credibilityReasons（落库的静态评估）
 * 与 freshnessLabel/freshHours（响应时实时计算的新鲜度）。
 */
export const searchNews = (payload) => request.post('/news/search', payload)

/**
 * 手动增量刷新全市场资讯（抓上游 → 去重落库）。
 *
 * <p>资讯雷达的"大盘"标签读的是库；库里的全市场内容只有 refresh 落过才有。
 * 所以大盘流首次为空时前端会自动触发一次（幂等：去重键保证重复刷新不重复入库）。
 */
export const refreshNews = () => request.post('/news/refresh')
