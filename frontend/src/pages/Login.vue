<script setup>
import { ref } from "vue"
import { useRouter } from "vue-router"
import { login, register } from "../api/auth.js"

const router = useRouter()
const isLogin = ref(true)
const form = ref({ username: '', password: '', email: '', phone: '' })
const error = ref('')

const toggleMode = () => {
  isLogin.value = !isLogin.value
  error.value = ''
}

const submit = async () => {
  try {
    error.value = ''
    const payload = isLogin.value
      ? { username: form.value.username, password: form.value.password }
      : {
          username: form.value.username,
          email: form.value.email,
          phone: form.value.phone,
          password: form.value.password
        }
    const res = isLogin.value
      ? await login(payload)
      : await register(payload)
    localStorage.setItem('token', res.data.token)
    router.push('/dashboard')
  } catch (e) {
    error.value = e.response?.data?.message || '操作失败'
  }
}
</script>

<template>
  <div class="login-container">
    <div class="login-card">
      <div class="brand">Stock Tracker</div>
      <h2>{{ isLogin ? '登录' : '注册' }}</h2>
      <form @submit.prevent="submit">
        <input v-model="form.username" placeholder="用户名 / 手机号 / 邮箱" required />
        <input v-if="!isLogin" v-model="form.email" placeholder="邮箱（选填）" />
        <input v-if="!isLogin" v-model="form.phone" placeholder="手机号（选填）" />
        <input v-model="form.password" type="password" placeholder="密码" required />
        <div v-if="isLogin" class="forgot-row">
          <span class="forgot" @click="error = '请联系管理员重置密码'">忘记密码？</span>
        </div>
        <p v-if="error" class="error">{{ error }}</p>
        <button type="submit">{{ isLogin ? '登录' : '注册' }}</button>
      </form>
      <p class="toggle" @click="toggleMode">
        {{ isLogin ? '没有账号？立即注册' : '已有账号？立即登录' }}
      </p>
    </div>
  </div>
</template>

<style scoped>
.login-container { display: flex; justify-content: center; align-items: center; min-height: 100vh; background: #f0f2f5; }
.login-card { background: white; padding: 40px; border-radius: 8px; box-shadow: 0 2px 12px rgba(0,0,0,0.1); width: 380px; }
.brand { text-align: center; font-size: 22px; font-weight: 700; color: #1a1a2e; margin-bottom: 8px; }
h2 { text-align: center; margin-bottom: 24px; color: #555; font-size: 16px; font-weight: 400; }
input { width: 100%; padding: 10px 12px; margin-bottom: 16px; border: 1px solid #d9d9d9; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
input:focus { outline: none; border-color: #4096ff; }
.forgot-row { text-align: right; margin-bottom: 16px; margin-top: -8px; }
.forgot { color: #1677ff; font-size: 13px; cursor: pointer; }
.forgot:hover { color: #4096ff; }
button { width: 100%; padding: 10px; background: #1677ff; color: white; border: none; border-radius: 4px; font-size: 16px; cursor: pointer; }
button:hover { background: #4096ff; }
.error { color: #ff4d4f; font-size: 13px; margin-bottom: 12px; }
.toggle { text-align: center; margin-top: 16px; color: #1677ff; cursor: pointer; font-size: 14px; }
.toggle:hover { color: #4096ff; }
</style>
