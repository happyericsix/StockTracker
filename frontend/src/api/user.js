import request from './request.js'

/** 生成 QQ 绑定验证码（需登录） */
export const generateBindCode = () => request.post('/user/bind-qq/code')

/** 查询当前用户绑定状态（需登录） */
export const getBindStatus = () => request.get('/user/bind-status')

/** 解绑（需登录） */
export const unbindQq = () => request.post('/user/unbind-qq')
