import request from './request.js'

/** 获取个人信息 */
export const getProfile = () => request.get('/user/profile')

/** 修改个人信息（email/phone/pushChannel/pushTarget） */
export const updateProfile = (data) => request.put('/user/profile', data)

/** 修改密码 */
export const changePassword = (data) => request.post('/user/change-password', data)

/** 推送连通性测试：按请求体里的配置发一条（保存前先验证 key 抄没抄对） */
export const testPush = (data) => request.post('/user/push/test', data)

/** 最近推送状态（时间倒序）：回答"推送到底有没有发出去、成没成功" */
export const getPushLog = (limit = 5) => request.get('/user/push/log', { params: { limit } })
