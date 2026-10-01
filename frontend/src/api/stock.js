import request from './request.js'

export const getStock = (symbol) => request.get(`/stocks/${symbol}`)
// 这里原有 getOverview（/stocks/{symbol}/overview）—— 全站无任何调用方，已移除。
// 后端端点仍在（README 有记录），需要时补一行即可。
export const getHistory = (symbol, page = 0, size = 100, period = 'day') =>
  request.get(`/stocks/${symbol}/history`, { params: { page, size, period } })
export const getMinuteKline = (symbol, period = 5) =>
  request.get(`/stocks/${symbol}/minute`, { params: { period } })

export const searchStock = (keyword, signal) =>
  request.get('/stocks/search', { params: { keyword }, signal })

// 自选股是**用户态数据**，挂在 /user/** 下（与 /user/profile 同一命名空间），
// 不再混在 /stocks/**（那是市场数据，且 /{stockSymbol} 是通配路径段）。
// 后端对应 FavoriteController；契约未变：GET 是裸数组，POST/DELETE 是 Result 信封。
export const getFavorites = () => request.get('/user/favorites')
export const addFavorite = (symbol, buyPrice, quantity) => request.post('/user/favorites', { symbol, buyPrice, quantity })
export const deleteFavorite = (symbol) => request.delete(`/user/favorites/${symbol}`)