import request from './request.js'

// signal（AbortController.signal）：K 线页切换标的/周期时取消上一次在途请求，
// 高频切换才不会把浏览器同源 6 个并发连接（SSE 已占 2 个）排满
export const getStock = (symbol, signal) => request.get(`/stocks/${symbol}`, { signal })
// 这里原有 getOverview（/stocks/{symbol}/overview）—— 全站无任何调用方，已移除。
// 后端端点仍在（README 有记录），需要时补一行即可。
export const getHistory = (symbol, page = 0, size = 100, period = 'day', signal) =>
  request.get(`/stocks/${symbol}/history`, { params: { page, size, period }, signal })
export const getMinuteKline = (symbol, period = 5, signal) =>
  request.get(`/stocks/${symbol}/minute`, { params: { period }, signal })

export const searchStock = (keyword, signal) =>
  request.get('/stocks/search', { params: { keyword }, signal })

// 自选股是**用户态数据**，挂在 /user/** 下（与 /user/profile 同一命名空间），
// 不再混在 /stocks/**（那是市场数据，且 /{stockSymbol} 是通配路径段）。
// 后端对应 FavoriteController；契约未变：GET 是裸数组，POST/DELETE 是 Result 信封。
export const getFavorites = () => request.get('/user/favorites')
// 纯库查询的自选代码列表（无行情/名称）：getFavorites 走行情网关（Redis 缓存），
// 缓存不可用会整体 500 —— 标签条这类场景用它降级，代码剥前缀显示。
export const getFavoritesBrief = () => request.get('/user/favorites/brief')
export const addFavorite = (symbol, buyPrice, quantity) => request.post('/user/favorites', { symbol, buyPrice, quantity })
export const deleteFavorite = (symbol) => request.delete(`/user/favorites/${symbol}`)
/** 估值分位序列（PE-TTM + 滚动 5 年分位，日线）。软失败：副图隐藏。 */
export const getValuation = (symbol) => request.get('/stocks/valuation', { params: { symbol } })
/** 个股主力资金流日线（腾讯源：主力=超大单+大单；四档仅当日）。软失败：卡片隐藏。 */
export const getStockFundFlow = (symbol, signal) =>
  request.get(`/stocks/${symbol}/fund-flow`, { signal })
