import request from './request.js'

/**
 * 同花顺扫码登录 / 绑定 相关接口。
 *
 * ⚠️ 扫码的两个接口（create/poll）在请求时**不发送本地 JWT**。
 *
 * 原因：request.js 的拦截器会无条件把 localStorage 里的 token 放进
 * Authorization 头（包括已经过期的）。后端虽然已对公开路径做了「容忍无效 token」
 * 处理，但带上一个必然无效的 token 没有意义，还会让服务端多走一次校验。
 *
 * 而「带 JWT」与「不带 JWT」在后端代表**两种不同行为**：
 *   - 带 JWT（已登录）→ 扫码 = 绑定到当前账号
 *   - 不带 JWT（未登录）→ 扫码 = 登录（找不到已有绑定就自动建号）
 *
 * 所以：
 *   createQr() / pollQr()              → 显式跳过 token，走"登录"语义
 *   createQrForBind() / pollQrForBind()→ 带 token，走"绑定"语义
 */

/** 登录页用：不带头，走"扫码登录"语义 */
export const createQr = () =>
  request.post('/ths/qr/create', null, { skipAuth: true })

/** 登录页用：轮询扫码状态（登录语义） */
export const pollQr = (qrSessionId) =>
  request.get('/ths/qr/poll', { params: { qrSessionId }, skipAuth: true })

/** 个人中心 / 主界面用：带 JWT，走"绑定到当前账号"语义 */
export const createQrForBind = () => request.post('/ths/qr/create')

/** 个人中心 / 主界面用：轮询（绑定语义） */
export const pollQrForBind = (qrSessionId) =>
  request.get('/ths/qr/poll', { params: { qrSessionId } })

/** 手动触发一次自选股同步 */
export const syncFavorites = () => request.post('/ths/sync')

/** 查询绑定状态（bound / lastSyncAt / lastError ...） */
export const getStatus = () => request.get('/ths/status')

/** 解绑（已同步的自选股会保留） */
export const unbind = () => request.delete('/ths/bind')
