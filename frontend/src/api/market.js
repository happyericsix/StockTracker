import request from './request.js'

/**
 * 市场状态：今天开不开市、休到哪天、这个价是哪一天的。
 *
 * 后端对应 `MarketController` 的 `GET /api/v1/market/status`，返回
 * `Result<MarketStatusResponse>` —— `request.js` 的响应拦截器已经把 Result 信封剥掉，
 * 所以调用方读 `res.data` 就是状态对象本身。
 *
 * ⚠️ 这个接口**不是装饰**。休市日腾讯行情返回的仍然是上一交易日的收盘价与涨跌幅，
 * 而行情接口自身没有任何信息能说明这一点 —— 没有这个接口，页面唯一的做法就是
 * 把周五的收盘价当成今天的数据展示（这正是用户报的那个 bug）。
 */
export const getMarketStatus = () => request.get('/market/status')

/**
 * 个股散户情绪：千股千评聚合指数（参与意愿/关注指数/评分）+ 对照验证结论。
 *
 * `res.data` 形状（Python sentiment_client 产出，Java 侧用快照历史补了
 * desire 的验证）：{desire:{latest,avg5,change}, focus:{latest}, score:{latest},
 * validation:{focus:{verdict}, score:{verdict}, desire:{verdict}}, disclaimer}。
 * ⚠️ 展示层必须带上验证结论与 disclaimer——未验证的情绪不许裸奔成"看多/看空"。
 */
export const getSentiment = (symbol) => request.get('/market/sentiment', { params: { symbol } })
