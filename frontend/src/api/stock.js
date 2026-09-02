import request from './request.js'

export const getStock = (symbol) => request.get(`/stocks/${symbol}`)
export const getOverview = (symbol) => request.get(`/stocks/${symbol}/overview`)
export const getHistory = (symbol, page = 0, size = 100, period = 'day') =>
  request.get(`/stocks/${symbol}/history`, { params: { page, size, period } })
export const getMinuteKline = (symbol, period = 5) =>
  request.get(`/stocks/${symbol}/minute`, { params: { period } })

export const searchStock = (keyword, signal) =>
  request.get('/stocks/search', { params: { keyword }, signal })

export const getFavorites = () => request.get('/stocks/favorites')
export const addFavorite = (symbol, buyPrice, quantity) => request.post('/stocks/favorites', { symbol, buyPrice, quantity })
export const deleteFavorite = (symbol) => request.delete(`/stocks/favorites/${symbol}`)