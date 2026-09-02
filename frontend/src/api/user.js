import request from './request.js'

/** 获取个人信息 */
export const getProfile = () => request.get('/user/profile')

/** 修改密码 */
export const changePassword = (data) => request.post('/user/change-password', data)
