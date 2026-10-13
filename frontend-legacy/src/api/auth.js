import request from './request.js'

export const login = (data) => request.post('/auth/login', data)
export const register = (data) => request.post('/auth/register', data)
