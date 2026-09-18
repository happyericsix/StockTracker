<script setup>
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { sendChat, getChatHistory } from '../api/messages.js'
import { messageBus } from '../composables/messageBus.js'

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

/**
 * "正在输入"的兜底时限。
 *
 * 为什么必须有：Java 侧调 Python 的超时是 60 秒，超时后它会用兜底文案**推送**一条消息，
 * 所以正常情况下 90 秒还没收到回复，说明不是模型在想，而是**推送链路断了** ——
 * 那时如果只把气泡留在那里转，用户唯一能做的就是干等。
 */
const TYPING_TIMEOUT_MS = 90000

const scrollToBottom = async () => {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

const formatTime = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`
}

const escapeHtml = (text) => text
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')
  .replace(/'/g, '&#39;')

const renderInline = (text) => text
  .replace(/`([^`\n]+)`/g, '<code>$1</code>')
  .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
  .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>')

const renderMarkdown = (raw) => {
  if (!raw || typeof raw !== 'string') return ''
  const lines = escapeHtml(raw).split('\n')
  const blocks = []
  let paragraph = []
  let list = null
  let codeLines = null

  const flushParagraph = () => {
    if (paragraph.length === 0) return
    blocks.push(`<p>${paragraph.map(renderInline).join('<br>')}</p>`)
    paragraph = []
  }
  const flushList = () => {
    if (!list) return
    blocks.push(`<ul>${list.join('')}</ul>`)
    list = null
  }

  for (const line of lines) {
    if (codeLines !== null) {
      if (/^```/.test(line)) {
        blocks.push(`<pre><code>${codeLines.join('\n')}</code></pre>`)
        codeLines = null
      } else {
        codeLines.push(line)
      }
      continue
    }

    if (/^```/.test(line)) {
      flushParagraph()
      flushList()
      codeLines = []
      continue
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/)
    if (heading) {
      flushParagraph()
      flushList()
      const level = heading[1].length
      blocks.push(`<h${level}>${renderInline(heading[2])}</h${level}>`)
      continue
    }

    const bullet = line.match(/^\s*[-*+]\s+(.+)$/)
    if (bullet) {
      flushParagraph()
      if (!list) list = []
      list.push(`<li>${renderInline(bullet[1])}</li>`)
      continue
    }

    if (line.trim() === '') {
      flushParagraph()
      flushList()
      continue
    }

    flushList()
    paragraph.push(line)
  }

  flushParagraph()
  flushList()
  return blocks.join('')
}

const loadHistory = async () => {
  try {
    const res = await getChatHistory()
    const list = res.data || []
    messages.value = Array.isArray(list) ? list : []
    await scrollToBottom()
  } catch (e) {
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
    // 乐观气泡 id 是本地 'local-<ts>'，与服务端 SSE 回显的数据库自增 id 永不相等，
    // 纯按 id 替换永远不命中 → 同一条消息会渲染两份。改为按内容定位替换。
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

/** 发出消息后启动兜底计时：超时不是"模型还在想"，而是推送断了 */
const startTypingWatch = () => {
  clearTypingWatch()
  typingTimer = setTimeout(async () => {
    typingTimer = null
    typing.value = false
    notice.value = '超过 90 秒没收到回复，可能是连接中断了 —— 已为你重新同步对话记录。'
    await loadHistory()
  }, TYPING_TIMEOUT_MS)
}

/** SSE 断线重连成功：断线期间的推送不会补发，必须自己再拉一次 */
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
  } catch (e) {
    return null
  }
}

const goStrategies = () => router.push('/strategies')
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
    read: true
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
  } catch (e) {
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
  <div class="chat-page">
    <header class="chat-header">
      <button class="back-btn" @click="router.push('/dashboard')">← 返回</button>
      <!-- 头像只是装饰：机器人身份已由右侧「智能助手」文字承载 -->
      <div class="bot-avatar" aria-hidden="true">🤖</div>
      <div class="bot-info">
        <!-- 本页唯一的一级标题：原来是无标题的 div，页面标题大纲里什么都没有 -->
        <h1 class="bot-name">智能助手</h1>
        <div class="bot-status">在线 · 行情分析中</div>
      </div>
    </header>

    <div class="chat-body" ref="listEl">
      <div v-if="messages.length === 0 && !typing" class="chat-empty">
        <div class="empty-icon" aria-hidden="true">🤖</div>
        <p>你好，我是智能助手</p>
        <p class="empty-hint">可以问我：现价查询、走势预测、持仓建议…</p>
      </div>

      <div
        v-for="m in messages"
        :key="m.id"
        class="msg-row"
        :class="m.type === 'CHAT_USER' ? 'mine' : 'theirs'"
      >
        <div v-if="m.type !== 'CHAT_USER'" class="avatar">🤖</div>
        <div class="bubble-wrap">
        <div v-if="m.type !== 'CHAT_USER'" class="bubble markdown-body" v-html="renderMarkdown(m.content)"></div>
        <div v-else class="bubble">{{ m.content }}</div>
          <div class="time num">{{ formatTime(m.createdAt) }}</div>
          <div v-if="m.type !== 'CHAT_USER' && (getStrategyId(m) || hasStrategyContent(m.content))" class="strategy-action">
            <button class="strategy-btn" @click="goStrategy(m)">{{ getStrategyId(m) ? '查看回测与策略详情' : '查看策略库' }}</button>
          </div>
        </div>
        <div v-if="m.type === 'CHAT_USER'" class="avatar mine-avatar">我</div>
      </div>

      <div v-if="typing" class="msg-row theirs">
        <div class="avatar">🤖</div>
        <div class="bubble typing-bubble">
          <span></span><span></span><span></span>
        </div>
      </div>

      <p v-if="notice" class="chat-notice">
        <span>{{ notice }}</span>
        <button class="notice-dismiss" type="button" @click="notice = ''">知道了</button>
      </p>
      <p v-if="error" class="chat-error">{{ error }}</p>
    </div>

    <footer class="chat-input-bar">
      <input
        v-model="input"
        class="chat-input"
        aria-label="输入消息"
        placeholder="输入消息，回车发送…"
        @keydown.enter="onEnter"
        @compositionstart="composing = true"
        @compositionend="composing = false"
      />
      <button class="send-btn" :disabled="sending" @click="send">发送</button>
    </footer>
  </div>
</template>

<style scoped>
.chat-page {
  display: flex;
  flex-direction: column;
  /* 移动端地址栏会被算进 100vh，用 dvh 兜底 */
  height: 100vh;
  height: 100dvh;
  background: var(--color-bg-page);
  /* App.vue 的安装横幅固定在底部（bottom: 20px + 安全区，高约 68px），
     这里在页面底部预留 88px，保证横幅出现时不会盖住固定的 .chat-input-bar */
  padding-bottom: 88px;
}
.chat-header {
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.back-btn {
  background: transparent;
  border: 1px solid var(--color-border-control);
  color: var(--color-text-inverse);
  padding: 6px 12px;
  border-radius: var(--radius-sm);
  cursor: pointer;
  font-size: 13px;
}
.bot-avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  /* 纯装饰圆底：改用达标主色，避免 #1677ff 配白字只有 4.10:1 */
  background: var(--color-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}
.bot-info { display: flex; flex-direction: column; }
.bot-name { font-size: 15px; font-weight: 600; }
.bot-status { font-size: 11px; color: var(--color-text-inverse-muted); margin-top: 1px; }

.chat-body {
  flex: 1;
  overflow-y: auto;
  padding: 16px 12px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.chat-empty {
  text-align: center;
  color: var(--color-text-muted);
  margin-top: 40px;
}
.empty-icon { font-size: 48px; margin-bottom: 8px; }
/* 旧值 #bbb 对灰底只有 1.71:1，全站最差的一处 */
.empty-hint { font-size: 12px; margin-top: 4px; color: var(--color-text-muted); }

.msg-row {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  max-width: 78%;
}
.msg-row.mine { align-self: flex-end; flex-direction: row-reverse; }
.avatar {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.mine-avatar { background: var(--color-warning); font-size: 12px; }

.bubble-wrap { display: flex; flex-direction: column; gap: 3px; }
.bubble {
  padding: 9px 13px;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
  white-space: pre-wrap;
}
.msg-row.theirs .bubble {
  background: var(--color-bg-surface);
  border-radius: 4px 12px 12px 12px;
  color: var(--color-text-primary);
  box-shadow: var(--shadow-1);
}
.msg-row.mine .bubble {
  /* 旧值 #1677ff 配白字只有 4.10:1，不达 AA */
  background: var(--color-accent);
  border-radius: 12px 4px 12px 12px;
  color: var(--color-text-on-accent);
}
.bubble.markdown-body {
  white-space: normal;
  line-height: 1.6;
}
.bubble.markdown-body :deep(p) { margin: 4px 0; }
.bubble.markdown-body :deep(h1),
.bubble.markdown-body :deep(h2),
.bubble.markdown-body :deep(h3),
.bubble.markdown-body :deep(h4),
.bubble.markdown-body :deep(h5),
.bubble.markdown-body :deep(h6) {
  margin: 10px 0 4px;
  line-height: 1.4;
  font-weight: 600;
}
.bubble.markdown-body :deep(h1) { font-size: 17px; }
.bubble.markdown-body :deep(h2) { font-size: 16px; }
.bubble.markdown-body :deep(h3) { font-size: 15px; }
.bubble.markdown-body :deep(h4),
.bubble.markdown-body :deep(h5),
.bubble.markdown-body :deep(h6) { font-size: 14px; }
.bubble.markdown-body :deep(ul) {
  margin: 4px 0;
  padding-left: 18px;
}
.bubble.markdown-body :deep(li) { margin: 2px 0; }
.bubble.markdown-body :deep(code) {
  background: var(--color-border);
  border-radius: 3px;
  padding: 1px 4px;
  font-size: 12px;
}
.bubble.markdown-body :deep(pre) {
  margin: 8px 0;
  background: var(--color-bg-subtle);
  border-radius: var(--radius-sm);
  padding: 8px 10px;
  overflow-x: auto;
  white-space: pre-wrap;
  word-break: break-word;
}
.bubble.markdown-body :deep(pre code) {
  background: transparent;
  padding: 0;
}
.time { font-size: 10px; color: var(--color-text-muted); }
.msg-row.mine .time { text-align: right; }

.strategy-action { margin-top: 4px; }
.strategy-btn {
  background: var(--color-bg-subtle);
  color: var(--color-success);
  border: 1px solid var(--color-success-mark);
  border-radius: var(--radius-sm);
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
}
.strategy-btn:hover { background: var(--color-bg-page); }

.typing-bubble { display: flex; align-items: center; gap: 4px; }
.typing-bubble span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-placeholder);
  animation: blink 1.2s infinite;
}
.typing-bubble span:nth-child(2) { animation-delay: 0.2s; }
.typing-bubble span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 60%, 100% { opacity: 0.25; }
  30% { opacity: 1; }
}

.chat-error { color: var(--color-danger); font-size: 12px; text-align: center; }

/* 兜底提示（例如"超时未收到回复"）：比错误轻、比静默重 —— 用户至少知道发生了什么 */
.chat-notice {
  display: flex;
  align-items: center;
  gap: 8px;
  justify-content: center;
  font-size: 12px;
  color: var(--color-warning);
  background: var(--color-bg-subtle);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  padding: 6px 10px;
  text-align: left;
}

.notice-dismiss {
  flex-shrink: 0;
  font-size: 12px;
  color: var(--color-text-secondary);
  background: transparent;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 2px 8px;
  cursor: pointer;
}

.notice-dismiss:hover { border-color: var(--color-border-strong); }

.chat-input-bar {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  background: var(--color-bg-surface);
  border-top: 1px solid var(--color-border);
  flex-shrink: 0;
  /* 固定底栏避开 iOS 手势条 */
  margin-bottom: env(safe-area-inset-bottom, 0px);
}
.chat-input {
  flex: 1;
  border: 1px solid var(--color-border-control);
  border-radius: 18px;
  padding: 9px 14px;
  font-size: 14px;
}
/* 焦点：删掉 outline:none，交给全局 2px 主色焦点环；边框变色只作第二通道 */
.chat-input:focus-visible { border-color: var(--color-accent); }
.send-btn {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: none;
  border-radius: 18px;
  padding: 0 20px;
  font-size: 14px;
  cursor: pointer;
}
.send-btn:disabled { background: var(--color-accent-soft); color: var(--color-text-muted); cursor: not-allowed; }
</style>
