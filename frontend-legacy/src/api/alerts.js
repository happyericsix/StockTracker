import request from './request.js'

/** 获取预警列表 */
export const getAlerts = () => request.get('/alerts')

/** 添加预警 */
export const addAlert = (data) => request.post('/alerts', data)

/** 修改预警（含开关） */
export const updateAlert = (id, data) => request.put(`/alerts/${id}`, data)

/** 删除预警 */
export const deleteAlert = (id) => request.delete(`/alerts/${id}`)
