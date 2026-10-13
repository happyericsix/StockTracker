<script setup>
/**
 * 同花顺扫码组件（登录页 + 主界面绑定共用）。逻辑自旧版原样移植。
 *
 * 两种模式，区别只在**要不要带本地 JWT**：
 *   mode="login" → 不带 JWT，语义是"扫码登录"（没账号会自动建号）
 *   mode="bind"  → 带 JWT，语义是"绑定到当前登录账号"
 */
import { ref, onMounted, onBeforeUnmount, computed } from 'vue'
import QRCode from 'qrcode'
import { Check } from 'lucide-vue-next'
import { createQr, pollQr, createQrForBind, pollQrForBind } from '../api/ths.js'
import Button from './ui/button.vue'

const props = defineProps({
  mode: { type: String, default: 'login' },   // 'login' | 'bind'
  autoStart: { type: Boolean, default: true },
})
const emit = defineEmits(['success', 'error'])

const canvasRef = ref(null)
const qrUrl = ref('')
const status = ref('idle')        // idle | loading | waiting | ok | expired | error
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
        firstLogin: data.firstLogin,
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
  <div class="flex flex-col items-center gap-2.5">
    <!-- 二维码区域：两主题下都保持白底（扫码要的是对比度，不是主题一致） -->
    <div
      class="flex h-[216px] w-[216px] items-center justify-center rounded-lg border border-border bg-white p-2"
    >
      <canvas
        v-show="status === 'waiting'"
        ref="canvasRef"
        role="img"
        :aria-label="isBind
          ? '同花顺绑定二维码，请用手机同花顺 App 扫码绑定当前账号'
          : '同花顺登录二维码，请用手机同花顺 App 扫码登录'"
      />

      <div v-if="status === 'loading'" class="text-[13px] text-ink-2">正在获取二维码...</div>

      <div v-if="status === 'expired'" class="flex flex-col items-center gap-2.5 p-4 text-center">
        <p class="text-[13px] text-ink-2">二维码已过期</p>
        <Button size="sm" variant="primary" @click="start">点击刷新</Button>
      </div>

      <div v-if="status === 'error'" class="flex flex-col items-center gap-2.5 p-4 text-center">
        <p class="text-[13px] text-ink-2">{{ message }}</p>
        <Button size="sm" variant="primary" @click="start">重试</Button>
      </div>

      <div v-if="status === 'ok'" class="flex items-center gap-1.5 p-4 text-[14px] font-medium text-down">
        <Check :size="16" :stroke-width="2" aria-hidden="true" /> {{ message }}
      </div>
    </div>

    <!-- 状态提示 -->
    <p v-if="status === 'waiting'" class="text-center text-[13px] text-ink-2">
      {{ isBind ? '请用手机同花顺 App 扫码，确认后即绑定到当前账号' : '请用手机同花顺 App 扫码登录' }}
      <span class="num ml-1 text-ink-3">{{ countdown }}s</span>
    </p>
    <p v-else-if="status === 'loading'" class="text-[13px] text-ink-2">请稍候...</p>
    <p
      v-else-if="message && status !== 'ok'"
      class="text-center text-[13px] text-up"
      role="status"
    >{{ message }}</p>

    <!-- 扫不出去时的兜底：直接给 URL -->
    <details v-if="qrUrl && status === 'waiting'" class="w-full text-[12px] text-ink-3">
      <summary class="cursor-pointer text-center">扫不出来？点这里</summary>
      <p class="mt-1.5 break-all rounded-md bg-surface-2 px-2 py-1.5 font-mono text-[11px] text-ink-2">{{ qrUrl }}</p>
      <p class="mt-1 text-center">把上面这行贴到任意「在线二维码生成器」即可</p>
    </details>
  </div>
</template>
