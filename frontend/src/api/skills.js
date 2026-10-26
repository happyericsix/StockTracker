import request from './request.js'

/** 技能记忆（④步）：列表/创建/修订/启停/范围/删草稿。全部软失败由页面处理。 */
export const listSkills = () => request.get('/skills')
export const createSkill = (data) => request.post('/skills', data)
export const reviseSkill = (id, data) => request.put(`/skills/${id}`, data)
export const setSkillStatus = (id, status) => request.post(`/skills/${id}/status`, { status })
export const setSkillScope = (id, data) => request.post(`/skills/${id}/scope`, data)
/** 删除草稿（仅 draft 可删；启用过的走停用，保留审计链）。 */
export const deleteSkill = (id) => request.delete(`/skills/${id}`)
