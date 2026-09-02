<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getProfile, changePassword } from '../api/user.js'

const router = useRouter()
const profile = ref({ username: '' })
const loading = ref(false)

// 修改密码
const showPasswordForm = ref(false)
const passwordForm = ref({ oldPassword: '', newPassword: '', confirmPassword: '' })
const pwdError = ref('')
const pwdSuccess = ref('')

const fetchProfile = async () => {
  try {
    const res = await getProfile()
    if (res.data) {
      profile.value = res.data
    }
  } catch (e) {
    // 如果后端暂无接口，使用 localStorage 兜底
    const stored = localStorage.getItem('user_profile')
    if (stored) {
      try { profile.value = JSON.parse(stored) } catch {}
    }
  }
}

const handleChangePassword = async () => {
  pwdError.value = ''
  pwdSuccess.value = ''
  if (passwordForm.value.newPassword !== passwordForm.value.confirmPassword) {
    pwdError.value = '两次输入的密码不一致'
    return
  }
  if (passwordForm.value.newPassword.length < 6) {
    pwdError.value = '密码长度至少6位'
    return
  }
  loading.value = true
  try {
    await changePassword({
      oldPassword: passwordForm.value.oldPassword,
      newPassword: passwordForm.value.newPassword
    })
    pwdSuccess.value = '密码修改成功'
    passwordForm.value = { oldPassword: '', newPassword: '', confirmPassword: '' }
    showPasswordForm.value = false
  } catch (e) {
    pwdError.value = e.response?.data?.message || '密码修改失败'
  } finally {
    loading.value = false
  }
}

const handleLogout = () => {
  localStorage.removeItem('token')
  localStorage.removeItem('user_profile')
  router.push('/login')
}

const goBack = () => router.push('/dashboard')

onMounted(fetchProfile)
</script>

<template>
  <div class="profile-page">
    <header>
      <button class="back-btn" @click="goBack">← 返回</button>
      <h1>个人中心</h1>
    </header>
    <main>
      <!-- 基本信息 -->
      <section class="card">
        <h2>基本信息</h2>
        <div class="info-grid">
          <div class="info-item">
            <span class="label">用户名</span>
            <span class="value">{{ profile.username || '-' }}</span>
          </div>
        </div>
      </section>

      <!-- 修改密码 -->
      <section class="card">
        <h2>安全设置</h2>
        <div v-if="!showPasswordForm">
          <button class="btn btn-outline" @click="showPasswordForm = true">修改密码</button>
        </div>
        <form v-else @submit.prevent="handleChangePassword" class="password-form">
          <input v-model="passwordForm.oldPassword" type="password" placeholder="当前密码" required />
          <input v-model="passwordForm.newPassword" type="password" placeholder="新密码（至少6位）" required />
          <input v-model="passwordForm.confirmPassword" type="password" placeholder="确认新密码" required />
          <p v-if="pwdError" class="error">{{ pwdError }}</p>
          <p v-if="pwdSuccess" class="success">{{ pwdSuccess }}</p>
          <div class="edit-actions">
            <button type="button" class="btn btn-text" @click="showPasswordForm = false">取消</button>
            <button type="submit" class="btn btn-primary" :disabled="loading">
              {{ loading ? '修改中...' : '确认修改' }}
            </button>
          </div>
        </form>
      </section>

      <!-- 退出登录 -->
      <section class="card">
        <button class="btn btn-danger btn-full" @click="handleLogout">退出登录</button>
      </section>
    </main>
  </div>
</template>

<style scoped>
.profile-page { min-height: 100vh; background: #f0f2f5; }
header { background: #1a1a2e; color: white; padding: 16px 24px; display: flex; align-items: center; gap: 16px; }
header h1 { margin: 0; font-size: 20px; }
.back-btn { background: transparent; border: 1px solid white; color: white; padding: 6px 16px; border-radius: 4px; cursor: pointer; }
.back-btn:hover { background: rgba(255,255,255,0.1); }
main { max-width: 600px; margin: 0 auto; padding: 24px 16px; }

.card { background: white; border-radius: 8px; padding: 24px; margin-bottom: 16px; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }
.card h2 { font-size: 16px; margin-bottom: 16px; color: #1a1a2e; }

.info-grid { display: flex; flex-direction: column; gap: 12px; }
.info-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid #f0f0f0; }
.info-item .label { color: #888; font-size: 14px; }
.info-item .value { color: #333; font-size: 14px; font-weight: 500; }

.password-form { display: flex; flex-direction: column; gap: 12px; }
.password-form input { width: 100%; padding: 10px 12px; border: 1px solid #d9d9d9; border-radius: 4px; font-size: 14px; box-sizing: border-box; }
.password-form input:focus { outline: none; border-color: #4096ff; }

.edit-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }

.btn { padding: 8px 20px; border: none; border-radius: 4px; font-size: 14px; cursor: pointer; transition: all 0.2s; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: #1677ff; color: white; }
.btn-primary:hover:not(:disabled) { background: #4096ff; }
.btn-danger { background: #ff4d4f; color: white; }
.btn-danger:hover:not(:disabled) { background: #ff7875; }
.btn-outline { background: transparent; border: 1px solid #d9d9d9; color: #333; }
.btn-outline:hover { border-color: #1677ff; color: #1677ff; }
.btn-text { background: transparent; color: #666; }
.btn-text:hover { color: #333; }
.btn-full { width: 100%; padding: 12px; font-size: 15px; }

.error { color: #ff4d4f; font-size: 13px; }
.success { color: #52c41a; font-size: 13px; }
</style>
