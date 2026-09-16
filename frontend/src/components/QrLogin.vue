<script setup>
/**
 * 同花顺扫码组件（登录页 + 主界面绑定共用）
 *
 * 两种模式，区别只在**要不要带本地 JWT**：
 *   mode="login" → 不带 JWT，语义是"扫码登录"（没账号会自动建号）
 *   mode="bind"  → 带 JWT，语义是"绑定到当前登录账号"
 *
 * 用法：
 *   <QrLogin mode="login" @success="onLogin" />
 *   <QrLogin mode="bind"  @success="onBound" />
 *
 * 事件：
 *   success({ token, username, firstLogin })  —— 扫码确认后触发
 *   error(message)                            —— 失败时触发
 */
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import QRCode from 'qrcode'
import { createQr, pollQr, createQrForBind, pollQrForBind } from '../api/ths.js'

const props = defineProps({
  mode: { type: String, default: 'login' },   // 'login' | 'bind'
  autoStart: { type: Boolean, default: true }
})
const emit = defineEmits(['success', 'error'])

const canvasRef = ref(null)
const qrUrl = ref('')
const status = ref('idle')        // idle | loading | waiting | confirming | ok | expired | error
const message = ref('')
const countdown = ref(0)
const qrSessionId = ref('')

let pollTimer = null
let countdownTimer = null
const isBind = computed(() => props.mode === 'bind')

/** 生成二维码并在 canvas 上画出来 */
const start = async () => {
  stopTimers()
  status.value = 'loading'
  message.value = ''
  try {
    const res = isBind.value ? await createQrForBind() : await createQr()
    const data = res.data
    qrSessionId.value = data.qrSessionId
    qrUrl.value = data.qrUrl

    // 在 canvas 上绘制二维码
    if (canvasRef.value) {
      await QRCode.toCanvas(canvasRef.value, data.qrUrl, { width: 200, margin: 1 })
    }

    status.value = 'waiting'
    countdown.value = data.expiresInSec || 120

    // 倒计时（纯展示；真正的过期判断在轮询结果里）
    countdownTimer = setInterval(() => {
      countdown.value = Math.max(0, countdown.value - 1)
      if (countdown.value === 0) {
        stopTimers()
        status.value = 'expired'
        message.value = '二维码已过期，请点击刷新'
      }
    }, 1000)

    // 按后端给的建议间隔轮询
    const interval = data.pollIntervalMs || 4000
    pollTimer = setInterval(poll, interval)
  } catch (e) {
    status.value = 'error'
    message.value = e.response?.data?.message || '获取二维码失败，请重试'
    emit('error', message.value)
  }
}

/** 轮询一次扫码状态 */
const poll = async () => {
  if (!qrSessionId.value) return
  try {
    const res = isBind.value
      ? await pollQrForBind(qrSessionId.value)
      : await pollQr(qrSessionId.value)
    const data = res.data

    if (data.status === 'pending') {
      status.value = 'waiting'
      return
    }
    if (data.status === 'ok') {
      // 绑定模式后端也会返回 token（是当前用户的），这里只用用户名
      stopTimers()
      status.value = 'ok'
      message.value = isBind.value ? '绑定成功！' : '登录成功，正在进入...'
      emit('success', {
        token: data.token,
        username: data.username,
        firstLogin: data.firstLogin
      })
      return
    }
    if (data.status === 'expired') {
      stopTimers()
      status.value = 'expired'
      message.value = data.message || '二维码已过期，请点击刷新'
    }
  } catch (e) {
    // 轮询失败（比如会话被后端清掉）不打断整体流程，等下一轮或让用户刷新
    const msg = e.response?.data?.message
    if (msg && msg.includes('过期')) {
      stopTimers()
      status.value = 'expired'
      message.value = msg
    }
  }
}

const stopTimers = () => {
  if (pollTimer) { clearInterval(pollTimer); pollTimer = null }
  if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null }
}

onMounted(() => {
  if (props.autoStart) start()
})
onBeforeUnmount(stopTimers)

defineExpose({ start, stop: stopTimers })
</script>

<template>
  <div class="qr-wrap">
    <!-- 二维码区域 -->
    <div class="qr-box">
      <!-- canvas 没有 alt：它是功能性图形（扫一下就是登录/绑定），
           用 role="img" + aria-label 描述要做的动作。倒计时/状态另由下面的提示承担。 -->
      <canvas
        v-show="status === 'waiting' || status === 'confirming'"
        ref="canvasRef"
        role="img"
        :aria-label="isBind
          ? '同花顺绑定二维码，请用手机同花顺 App 扫码绑定当前账号'
          : '同花顺登录二维码，请用手机同花顺 App 扫码登录'"
      />

      <div v-if="status === 'loading'" class="qr-placeholder">正在获取二维码...</div>

      <div v-if="status === 'expired'" class="qr-placeholder">
        <p class="ph-text">二维码已过期</p>
        <button type="button" class="qr-btn" @click="start">点击刷新</button>
      </div>

      <div v-if="status === 'error'" class="qr-placeholder">
        <p class="ph-text">{{ message }}</p>
        <button type="button" class="qr-btn" @click="start">重试</button>
      </div>

      <div v-if="status === 'ok'" class="qr-placeholder qr-ok">
        <p class="ph-text"><span aria-hidden="true">✓</span> {{ message }}</p>
      </div>
    </div>

    <!-- 状态提示 -->
    <p v-if="status === 'waiting'" class="qr-tip">
      {{ isBind ? '请用手机同花顺 App 扫码，确认后即绑定到当前账号' : '请用手机同花顺 App 扫码登录' }}
      <span class="qr-count num">{{ countdown }}s</span>
    </p>
    <p v-else-if="status === 'loading'" class="qr-tip">请稍候...</p>
    <!-- 过期/失败是动态插入的提示，用 role="status" 让屏幕阅读器也能听到；
         这里没有每秒跳动的倒计时，不会反复播报。 -->
    <p v-else-if="message && status !== 'ok'" class="qr-tip qr-tip-err" role="status">{{ message }}</p>

    <!-- 扫不出去时的兜底：直接给 URL -->
    <details v-if="qrUrl && status === 'waiting'" class="qr-fallback">
      <summary>扫不出来？点这里</summary>
      <p class="qr-url">{{ qrUrl }}</p>
      <p class="qr-hint">把上面这行贴到任意「在线二维码生成器」即可</p>
    </details>
  </div>
</template>

<style scoped>
.qr-wrap { display: flex; flex-direction: column; align-items: center; gap: 10px; }
.qr-box {
  width: 216px; height: 216px; display: flex; align-items: center; justify-content: center;
  border: 1px solid var(--color-border); border-radius: var(--radius-md); background: var(--color-bg-subtle);
}
.qr-box canvas { display: block; }
.qr-placeholder { text-align: center; padding: 16px; }
.ph-text { color: var(--color-text-secondary); font-size: 13px; margin: 0 0 10px; }
.qr-ok .ph-text { color: var(--color-success); font-weight: 500; }
.qr-btn {
  padding: 6px 16px; background: var(--color-accent); color: var(--color-text-on-accent); border: none;
  border-radius: var(--radius-sm); font-size: 13px; cursor: pointer;
}
.qr-btn:hover { background: var(--color-accent-hover); }
/* 提示用次要文字、倒计时用最弱一级：都达 4.5:1，同时保留原来的两级层次
   （旧值 #666 / #999，其中 #999 对白仅 2.85:1） */
.qr-tip { font-size: 13px; color: var(--color-text-secondary); margin: 0; text-align: center; }
.qr-tip-err { color: var(--color-danger); }
.qr-count { color: var(--color-text-muted); margin-left: 4px; }
.qr-fallback { font-size: 12px; color: var(--color-text-muted); width: 100%; }
.qr-fallback summary { cursor: pointer; text-align: center; }
.qr-url {
  word-break: break-all; background: var(--color-bg-subtle); padding: 6px 8px;
  border-radius: var(--radius-sm); margin: 6px 0; font-family: monospace; font-size: 11px;
}
.qr-hint { margin: 0; text-align: center; }
</style>
