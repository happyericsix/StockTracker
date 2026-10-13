<script setup>
/**
 * 智能助手（全页对话）。逻辑自旧版原样移植：乐观气泡按内容定位替换、
 * 90 秒兜底计时（超时=推送断了，不是模型在想）、跨天分隔条、IME 组合态不发送。
 */
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Bot } from 'lucide-vue-next'
import { sendChat, getChatHistory } from '../api/messages.js'
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
const pendingLocalId = ref(null)
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
    await scrollToBottom()
  } catch {
    // 后端未就绪时静默，聊天记录为空
  }
}

const onBusMessage = (msg) => {
  if (!msg || !msg.id) return
  if (msg.type === 'CHAT_BOT') {
    typing.value = false
    clearTypingWatch()
    notice.value = ''
  }
  if (msg.type === 'CHAT_BOT' || msg.type === 'CHAT_USER') {
    // 乐观气泡 id 与数据库 id 永不相等，按内容定位替换
    if (msg.type === 'CHAT_USER' && pendingLocalId.value) {
      const pendingIndex = messages.value.findIndex((m) => m.id === pendingLocalId.value)
      if (pendingIndex !== -1 && messages.value[pendingIndex].content === msg.content) {
        messages.value.splice(pendingIndex, 1, msg)
        pendingLocalId.value = null
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
  pendingLocalId.value = tempMsg.id
  messages.value.push(tempMsg)
  scrollToBottom()
  typing.value = true
  sending.value = true
  notice.value = ''
  startTypingWatch()
  try {
    await sendChat(text)
  } catch {
    pendingLocalId.value = null
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
