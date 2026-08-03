<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { generateBindCode, getBindStatus, unbindQq } from '../api/user.js'

const router = useRouter()
const status = ref({ bound: false, qqNumber: null, username: null })
const code = ref('')
const loading = ref(false)
const error = ref('')
const success = ref('')
const countdown = ref(0)
let timer = null

const fetchStatus = async () => {
  try {
    const res = await getBindStatus()
    status.value = res.data || { bound: false }
  } catch (e) {
    error.value = e.response?.data?.message || '获取绑定状态失败'
  }
}

const handleGenerate = async () => {
  if (loading.value) return
  loading.value = true
  error.value = ''
  success.value = ''
  try {
    const res = await generateBindCode()
    code.value = res.data
    success.value = '验证码已生成，5 分钟内有效'
    startCountdown(300)
  } catch (e) {
    error.value = e.response?.data?.message || '生成验证码失败'
  } finally {
    loading.value = false
  }
}

const handleUnbind = async () => {
  if (!confirm(`确认解绑 QQ ${status.value.qqNumber}？`)) return
  loading.value = true
  error.value = ''
  try {
    await unbindQq()
    success.value = '解绑成功'
    code.value = ''
    countdown.value = 0
    if (timer) { clearInterval(timer); timer = null }
    await fetchStatus()
  } catch (e) {
    error.value = e.response?.data?.message || '解绑失败'
  } finally {
    loading.value = false
  }
}

const startCountdown = (seconds) => {
  countdown.value = seconds
  if (timer) clearInterval(timer)
  timer = setInterval(() => {
    countdown.value--
    if (countdown.value <= 0) {
      clearInterval(timer)
      timer = null
      code.value = ''
    }
  }, 1000)
}

const formatTime = (s) => {
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}:${sec.toString().padStart(2, '0')}`
}

const goBack = () => router.push('/dashboard')

onMounted(fetchStatus)
</script>

<template>
  <div class="bind-container">
    <div class="bind-card">
      <h2>🔗 绑定 QQ</h2>
      <p class="subtitle">绑定后即可在 QQ 机器人查行情、自选股、技术分析</p>

      <!-- 已绑定状态 -->
      <div v-if="status.bound" class="status-box success-box">
        <div class="status-icon">✅</div>
        <div class="status-content">
          <div class="status-title">已绑定</div>
          <div class="status-info">
            <span>QQ: <strong>{{ status.qqNumber }}</strong></span>
            <span>用户: <strong>{{ status.username }}</strong></span>
          </div>
        </div>
        <button class="btn btn-danger" @click="handleUnbind" :disabled="loading">
          {{ loading ? '处理中...' : '解绑' }}
        </button>
      </div>

      <!-- 未绑定状态 -->
      <div v-else class="status-box unbound-box">
        <div class="status-icon">📭</div>
        <div class="status-content">
          <div class="status-title">尚未绑定</div>
          <div class="status-info">点击下方按钮生成 6 位验证码</div>
        </div>
      </div>

      <!-- 生成验证码按钮（未绑定时显示） -->
      <div v-if="!status.bound" class="action-row">
        <button
          class="btn btn-primary"
          @click="handleGenerate"
          :disabled="loading || (countdown > 0 && !code)"
        >
          {{ loading ? '生成中...' : (countdown > 0 ? '重新生成' : '生成验证码') }}
        </button>
      </div>

      <!-- 验证码展示 -->
      <div v-if="code" class="code-box">
        <div class="code-label">你的验证码</div>
        <div class="code-value">{{ code }}</div>
        <div class="code-timer">
          ⏱ 剩余 {{ formatTime(countdown) }}（过期后需重新生成）
        </div>
      </div>

      <!-- 步骤说明 -->
      <div v-if="code" class="steps">
        <div class="step-title">📋 接下来：</div>
        <ol>
          <li>打开 QQ，找到机器人（股小盯）</li>
          <li>发送：<code>绑定 {{ code }}</code>（注意空格）</li>
          <li>机器人会回复「绑定成功」</li>
          <li>之后就可以在 QQ 查行情、自选股了 🎉</li>
        </ol>
      </div>

      <!-- 错误/成功提示 -->
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="success && !status.bound" class="success">{{ success }}</p>

      <button class="btn btn-text" @click="goBack">← 返回首页</button>
    </div>
  </div>
</template>

<style scoped>
.bind-container { display: flex; justify-content: center; align-items: center; min-height: 100vh; background: #f0f2f5; padding: 20px; }
.bind-card { background: white; padding: 40px; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.08); width: 480px; max-width: 100%; }
h2 { margin: 0 0 8px; color: #1a1a2e; font-size: 24px; }
.subtitle { color: #666; font-size: 14px; margin: 0 0 24px; }

.status-box { display: flex; align-items: center; padding: 16px; border-radius: 8px; margin-bottom: 20px; }
.success-box { background: #f6ffed; border: 1px solid #b7eb8f; }
.unbound-box { background: #fff7e6; border: 1px solid #ffd591; }
.status-icon { font-size: 32px; margin-right: 16px; }
.status-content { flex: 1; }
.status-title { font-weight: 600; font-size: 16px; margin-bottom: 4px; }
.status-info { font-size: 13px; color: #666; }
.status-info span { display: inline-block; margin-right: 16px; }

.action-row { margin-bottom: 20px; }
.btn { padding: 10px 20px; border: none; border-radius: 6px; font-size: 14px; cursor: pointer; transition: all 0.2s; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: #1677ff; color: white; width: 100%; padding: 12px; font-size: 15px; }
.btn-primary:hover:not(:disabled) { background: #4096ff; }
.btn-danger { background: #ff4d4f; color: white; }
.btn-danger:hover:not(:disabled) { background: #ff7875; }
.btn-text { background: transparent; color: #666; margin-top: 16px; width: 100%; }
.btn-text:hover { color: #333; }

.code-box { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 24px; border-radius: 8px; text-align: center; margin-bottom: 20px; }
.code-label { font-size: 13px; opacity: 0.9; margin-bottom: 8px; }
.code-value { font-size: 36px; font-weight: 700; letter-spacing: 8px; font-family: 'Courier New', monospace; }
.code-timer { font-size: 12px; margin-top: 8px; opacity: 0.9; }

.steps { background: #fafafa; padding: 16px; border-radius: 8px; margin-bottom: 16px; }
.step-title { font-weight: 600; margin-bottom: 12px; }
.steps ol { margin: 0; padding-left: 20px; line-height: 1.8; font-size: 14px; color: #333; }
.steps code { background: #f0f0f0; padding: 2px 6px; border-radius: 3px; font-family: monospace; color: #d4380d; }

.error { color: #ff4d4f; font-size: 13px; margin: 8px 0; }
.success { color: #52c41a; font-size: 13px; margin: 8px 0; }
</style>
