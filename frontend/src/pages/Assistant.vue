<script setup>
/**
 * 智能助手（全页对话）。逻辑自旧版原样移植：乐观气泡按内容定位替换、
 * 90 秒兜底计时（超时=推送断了，不是模型在想）、跨天分隔条、IME 组合态不发送。
 */
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Bot } from 'lucide-vue-next'
import { sendChat, getChatHistory } from '../api/messages.js'
import { getLlmConfig } from '../api/user.js'
import { messageBus } from '../composables/messageBus.js'
import { renderMarkdown } from '../utils/markdown.js'
import Button from '../components/ui/button.vue'

const router = useRouter()
const messages = ref([])
const input = ref('')
const typing = ref(false)
const sending = ref(false)
const error = ref('')
const notice = ref('')
const composing = ref(false)
const listEl = ref(null)
// BYOK：未绑定自己的 Key 时 AI 功能会降级为模板回复——进门就说清并给出路
const llmMissing = ref(false)
let unsubscribe = null
let unsubscribeReconnect = null
let typingTimer = null

/** "正在输入"的兜底时限：90 秒还没收到回复说明推送链路断了，不是模型在想 */
const TYPING_TIMEOUT_MS = 90000

const scrollToBottom = async () => {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

// 时间标度：今天只显示时分；跨天带日期，跨年带年份
const formatTime = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`
  const now = new Date()
  const sameDay = d.getFullYear() === now.getFullYear()
    && d.getMonth() === now.getMonth() && d.getDate() === now.getDate()
  if (sameDay) return hm
  if (d.getFullYear() === now.getFullYear()) {
    return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`
  }
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`
}

const dayKey = (iso) => {
  const d = new Date(iso)
  return isNaN(d.getTime()) ? `invalid-${iso}` : `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`
}

const dayLabel = (iso) => {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const now = new Date()
  const sameDay = dayKey(iso) === `${now.getFullYear()}-${now.getMonth()}-${now.getDate()}`
  if (sameDay) return '今天'
  const yesterday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - 1)
  if (dayKey(iso) === `${yesterday.getFullYear()}-${yesterday.getMonth()}-${yesterday.getDate()}`) {
    return '昨天'
  }
  if (d.getFullYear() === now.getFullYear()) return `${d.getMonth() + 1}月${d.getDate()}日`
  return `${d.getFullYear()}年${d.getMonth() + 1}月${d.getDate()}日`
}

const loadHistory = async () => {
  try {
    const res = await getChatHistory()
    const list = res.data || []
    messages.value = Array.isArray(list) ? list : []
    // 历史里只有落库的终态消息：任何本地流式气泡都不该活过这次整表替换
    streamingId.value = null
    await scrollToBottom()
  } catch {
    // 后端未就绪时静默，聊天记录为空
  }
}

/** 当前流式输出的 streamId（同一用户同一时刻只有一轮在流，后端轮次门保证） */
const streamingId = ref(null)

/** 撤掉流式气泡：终态消息落库推送、或轮次被新消息取代（CHAT_BOT_ABORT）时调用 */
const removeStreamingBubble = () => {
  if (!streamingId.value) return
  const idx = messages.value.findIndex((m) => m.id === streamingId.value)
  streamingId.value = null
  if (idx !== -1) messages.value.splice(idx, 1)
}

/** 流式增量（2026-10-20）：边生成边往气泡里追加，打字机效果 */
const onStreamDelta = (msg) => {
  if (!msg.streamId || !msg.content) return
  typing.value = false
  // 流式期间同样要有"断流 90 秒"兜底：增量还在来就不断续命
  startTypingWatch()
  if (streamingId.value === msg.streamId) {
    const target = messages.value.find((m) => m.id === msg.streamId)
    if (target) {
      target.content += msg.content
      scrollToBottom()
      return
    }
  }
  // 新一轮的流开始了：理论上旧气泡已被 ABORT 清掉，这里兜底再清一次
  removeStreamingBubble()
  streamingId.value = msg.streamId
  messages.value.push({
    id: msg.streamId,
    type: 'CHAT_BOT',
    content: msg.content,
    createdAt: new Date().toISOString(),
    read: true,
    streaming: true,
  })
  scrollToBottom()
}

const onBusMessage = (msg) => {
  if (!msg) return
  if (msg.type === 'CHAT_BOT_DELTA') {
    onStreamDelta(msg)
    return
  }
  if (msg.type === 'CHAT_BOT_ABORT') {
    // 轮次被新消息取代：半截气泡整体撤掉（内容没落库，留着只会误导）
    if (msg.streamId === streamingId.value) removeStreamingBubble()
    return
  }
  if (!msg.id) return
  if (msg.type === 'CHAT_BOT') {
    typing.value = false
    clearTypingWatch()
    notice.value = ''
    // 终态消息是权威内容（可能与流式半截不完全一致），落库版本替换流式气泡
    removeStreamingBubble()
  }
  if (msg.type === 'CHAT_BOT' || msg.type === 'CHAT_USER') {
    // 用户消息：SSE 回显与乐观气泡是同一条 —— 按内容定位换 id（乐观气泡 id 与
    // 数据库 id 永不相等）。连发多条时回显顺序不定，必须扫全部待合并气泡：
    // 旧实现只盯"最后一条 pending"，第二条发出后第一条的回显就匹配不上，气泡显示两遍。
    if (msg.type === 'CHAT_USER') {
      const pendingIndex = messages.value.findIndex(
        (m) => String(m.id).startsWith('local-') && m.content === msg.content)
      if (pendingIndex !== -1) {
        messages.value.splice(pendingIndex, 1, msg)
        scrollToBottom()
        return
      }
    }
    if (!messages.value.some((m) => m.id === msg.id)) {
      messages.value.push(msg)
      scrollToBottom()
    }
  }
}

const clearTypingWatch = () => {
  if (typingTimer) {
    clearTimeout(typingTimer)
    typingTimer = null
  }
}

const startTypingWatch = () => {
  clearTypingWatch()
  typingTimer = setTimeout(async () => {
    typingTimer = null
    typing.value = false
    notice.value = '超过 90 秒没收到回复，可能是连接中断了 —— 已为你重新同步对话记录。'
    await loadHistory()
  }, TYPING_TIMEOUT_MS)
}

const onReconnect = async () => {
  clearTypingWatch()
  typing.value = false
  await loadHistory()
}

const hasStrategyContent = (content) => {
  if (!content || typeof content !== 'string') return false
  const hasSchema = content.includes('"schema_version"')
  const hasJsonFence = content.includes('```json') || content.includes('```')
  const hasEntryExit = content.includes('"entry"') || content.includes('"exit"')
  return hasSchema && (hasJsonFence || hasEntryExit)
}

const getStrategyId = (msg) => {
  if (!msg || !msg.metadata) return null
  try {
    const meta = typeof msg.metadata === 'string' ? JSON.parse(msg.metadata) : msg.metadata
    return meta && meta.strategyId ? String(meta.strategyId) : null
  } catch {
    return null
  }
}

const goStrategy = (msg) => {
  const id = getStrategyId(msg)
  router.push(id ? `/strategies/${id}` : '/strategies')
}

const send = async () => {
  const text = input.value.trim()
  if (!text || sending.value) return
  error.value = ''
  input.value = ''
  const tempMsg = {
    id: 'local-' + Date.now(),
    type: 'CHAT_USER',
    content: text,
    createdAt: new Date().toISOString(),
    read: true,
  }
  messages.value.push(tempMsg)
  scrollToBottom()
  typing.value = true
  sending.value = true
  notice.value = ''
  startTypingWatch()
  try {
    await sendChat(text)
  } catch {
    typing.value = false
    clearTypingWatch()
    error.value = '发送失败，请稍后重试'
  } finally {
    sending.value = false
  }
}

const onEnter = () => {
  if (!composing.value) send()
}

onMounted(() => {
  loadHistory()
  messageBus.connect()
  unsubscribe = messageBus.subscribe(onBusMessage)
  unsubscribeReconnect = messageBus.subscribeReconnect(onReconnect)
  // 查询 Key 状态失败（如旧后端）不显示横幅，避免误报
  getLlmConfig().then((res) => {
    llmMissing.value = res.data?.enabled === true && !res.data?.configured
  }).catch(() => {})
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
  if (unsubscribeReconnect) unsubscribeReconnect()
  clearTypingWatch()
})
</script>

<template>
  <!-- 壳内全高：桌面扣容器上下留白；移动端再扣掉顶栏（52px） -->
  <div class="mx-auto flex h-[calc(100dvh-100px)] w-full max-w-[760px] flex-col lg:h-[calc(100dvh-48px)]">
    <header class="flex items-center gap-3 pb-3">
      <span class="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-on-accent">
        <Bot :size="21" :stroke-width="1.75" aria-hidden="true" />
      </span>
      <div>
        <h1 class="text-[16px] font-semibold text-ink">智能助手</h1>
        <div class="text-[12px] text-ink-3">在线 · 行情分析中</div>
      </div>
    </header>

    <!-- BYOK：没绑自己的 Key 时 AI 回复会降级为模板，进门就给出路 -->
    <p v-if="llmMissing" class="mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-[12.5px] text-warn" role="status">
      <span class="min-w-0 flex-1">还没绑定你自己的 AI Key——智能助手与交易委员会会降级为模板回复。</span>
      <button type="button" class="shrink-0 rounded-md border border-warn/50 px-2 py-0.5 text-warn transition-colors hover:bg-warn/10"
        @click="router.push('/profile')">去个人中心配置</button>
    </p>

    <div ref="listEl" class="flex flex-1 flex-col gap-3.5 overflow-y-auto overscroll-contain px-1 pb-2">
      <div v-if="messages.length === 0 && !typing" class="mt-12 flex flex-col items-center gap-2 text-center">
        <span class="flex h-14 w-14 items-center justify-center rounded-full bg-surface-2 text-ink-3">
          <Bot :size="30" :stroke-width="1.5" aria-hidden="true" />
        </span>
        <p class="text-[14px] text-ink-2">问我行情、资讯或策略</p>
        <p class="text-[12.5px] text-ink-3">例：茅台现在多少钱</p>
      </div>

      <template v-for="(m, i) in messages" :key="m.id">
        <!-- 跨天分隔条 -->
        <div
          v-if="i === 0 || dayKey(m.createdAt) !== dayKey(messages[i - 1].createdAt)"
          class="flex items-center gap-2.5 py-1 text-[11px] text-ink-3 select-none [&::after]:h-px [&::after]:flex-1 [&::after]:bg-border [&::before]:h-px [&::before]:flex-1 [&::before]:bg-border"
        >{{ dayLabel(m.createdAt) }}</div>
        <div class="flex max-w-[78%] items-start gap-2" :class="m.type === 'CHAT_USER' ? 'flex-row-reverse self-end' : ''">
          <span
            v-if="m.type !== 'CHAT_USER'"
            class="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent"
          >
            <Bot :size="16" :stroke-width="1.75" aria-hidden="true" />
          </span>
          <div class="flex flex-col gap-0.5">
            <div
              v-if="m.type !== 'CHAT_USER'"
              class="md-body rounded-xl rounded-tl-sm border border-border bg-surface px-3.5 py-2.5 text-[13.5px] leading-relaxed text-ink"
              :class="m.streaming ? 'is-streaming' : ''"
              v-html="renderMarkdown(m.content)"
            ></div>
            <div
              v-else
              class="whitespace-pre-wrap break-words rounded-xl rounded-tr-sm bg-accent px-3.5 py-2.5 text-[13.5px] leading-relaxed text-on-accent"
            >{{ m.content }}</div>
            <div class="num px-1 text-[10px] text-ink-3" :class="m.type === 'CHAT_USER' ? 'text-right' : ''">{{ formatTime(m.createdAt) }}</div>
            <div v-if="m.type !== 'CHAT_USER' && (getStrategyId(m) || hasStrategyContent(m.content))" class="mt-1">
              <button
                type="button"
                class="rounded-lg border border-border bg-surface-2 px-2.5 py-1 text-[12px] text-ink-2 transition-colors hover:border-accent hover:text-accent"
                @click="goStrategy(m)"
              >{{ getStrategyId(m) ? '查看回测与策略详情' : '查看策略库' }}</button>
            </div>
          </div>
          <span
            v-if="m.type === 'CHAT_USER'"
            class="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-2 text-[12px] font-medium text-ink-2"
          >我</span>
        </div>
      </template>

      <div v-if="typing" class="flex items-start gap-2">
        <span class="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-accent-soft text-accent">
          <Bot :size="16" :stroke-width="1.75" aria-hidden="true" />
        </span>
        <div class="flex items-center gap-1 rounded-xl rounded-tl-sm border border-border bg-surface px-4 py-3.5">
          <span class="typing-dot"></span><span class="typing-dot" style="animation-delay: 0.2s"></span><span class="typing-dot" style="animation-delay: 0.4s"></span>
        </div>
      </div>

      <p v-if="notice" class="flex items-center justify-center gap-2 rounded-lg border border-border bg-surface-2 px-2.5 py-1.5 text-left text-[12px] text-warn">
        <span class="flex-1">{{ notice }}</span>
        <button type="button" class="shrink-0 rounded-md border border-border px-2 py-0.5 text-[12px] text-ink-2" @click="notice = ''">知道了</button>
      </p>
      <p v-if="error" class="text-center text-[12px] text-up">{{ error }}</p>
    </div>

    <footer class="mt-2 flex gap-2 pb-[env(safe-area-inset-bottom)]">
      <input
        v-model="input"
        class="h-[40px] min-w-0 flex-1 rounded-full border border-border bg-surface px-4 text-[13.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
        aria-label="输入消息"
        placeholder="输入消息，回车发送…"
        @keydown.enter="onEnter"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      >
      <Button variant="primary" class="rounded-full px-5" :disabled="sending" @click="send">发送</Button>
    </footer>
    <p class="mt-1.5 text-center text-[11px] text-ink-3">AI 回复仅供参考，不构成投资建议</p>
  </div>
</template>

<style scoped>
/* 流式输出中的气泡：末尾一个呼吸的光标，告诉用户"还在生成" */
.is-streaming::after {
  content: '▍';
  margin-left: 1px;
  color: var(--ink-3);
  animation: typing 1.1s infinite ease-in-out;
}
.typing-dot {
  width: 6px;
  height: 6px;
  border-radius: 9999px;
  background: var(--ink-3);
  animation: typing 1.2s infinite ease-in-out;
}
@keyframes typing {
  0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}
</style>
