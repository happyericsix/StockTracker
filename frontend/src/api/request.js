import axios from 'axios'
import router from '../router/index.js'

const request = axios.create({
  baseURL: '/api/v1',
  headers: { 'Accept-Charset': 'utf-8' }
})

request.interceptors.request.use(config => {
  // 扫码登录用的接口显式跳过带 token —— 它们以"未登录"身份调用，
  // 语义是"扫码登录"；带上（可能已过期的）本地 token 没意义，见 api/ths.js 的说明。
  if (config.skipAuth) {
    delete config.headers.Authorization
    return config
  }
  const token = localStorage.getItem('token')
  if (token) {
    config.headers.Authorization = `Bearer ${token}`
  }
  return config
})

/**
 * 后端有两种响应形状，前端不该逐个端点去记：
 *
 *   1. Result 信封：{"code":200,"message":"success","data":<业务数据>}
 *      出现在 /alerts 的 DELETE、/chat/send、/messages 的两个 PUT、
 *      /stocks/search、/stocks/favorites 的 POST/DELETE，以及**整个** /strategies、/ths、/user
 *   2. 裸 DTO：直接就是业务对象或数组
 *      出现在 /alerts 的 GET/POST/PUT、/chat/history、/messages、/messages/unread-count、
 *      /stocks/{symbol} 及其 /overview /history /minute、/stocks/favorites 的 GET，以及 /auth/*
 *
 * 判据用"同时含 code、message、data 三个键"来识别信封。已核对不会被裸 DTO 误伤：
 *   AuthResponse{token,username,email,message} 有 message 但没有 code/data；
 *   PagedResponse{content,page,size,totalElements,totalPages} 三者都没有。
 *
 * 这里统一剥掉信封，业务代码一律只写 res.data，不必再判断端点属于哪一种。
 * 历史上这个不统一造成了两份各自重复的 unwrap() 兜底，以及一处静默取到 undefined 的风险。
 */
const isResultEnvelope = (body) =>
  body !== null &&
  typeof body === 'object' &&
  !Array.isArray(body) &&
  typeof body.code === 'number' &&
  'message' in body &&
  'data' in body

request.interceptors.response.use(
  response => {
    const body = response.data
    if (!isResultEnvelope(body)) return response

    // 业务失败。关键：Result.error(...) 是**带 HTTP 200** 返回的，
    // 不在这里转成 reject 的话，调用方会把失败当成功 ——
    // 例如删除不存在的自选股会静默"成功"、扫码失败会让调用方去读 null 的属性而抛 TypeError。
    if (body.code !== 200) {
      const err = new Error(body.message || '请求失败')
      err.isBusinessError = true
      // 暴露业务码：HTTP 状态码在这里是 200，靠它判断不了"该回滚还是该本地兜底"。
      // 调用方要读状态时用 `e.businessCode ?? e.response?.status`
      // （见 Alerts.vue 的 classifyError：4xx 类=用户错误要回滚，5xx 类=可重试走本地兜底）。
      err.businessCode = body.code
      // 保留**原始信封**（而不是剥离后的），这样 errorMessage(e) 里的
      // e.response?.data?.message 仍能读到后端的中文业务提示。
      err.response = response
      return Promise.reject(err)
    }

    // 成功：把信封里的业务数据提到 response.data，业务代码只认 res.data
    response.data = body.data
    return response
  },
  error => {
    if (error.response?.status === 401) {
      localStorage.removeItem('token')
      if (router.currentRoute.value.path !== '/login') {
        router.push('/login')
      }
    }
    return Promise.reject(error)
  }
)

export default request
