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
const composing = ref(false)
const listEl = ref(null)
let unsubscribe = null

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
  }
  if (msg.type === 'CHAT_BOT' || msg.type === 'CHAT_USER') {
    if (!messages.value.some((m) => m.id === msg.id)) {
      messages.value.push(msg)
      scrollToBottom()
    }
  }
}

const hasStrategyContent = (content) => {
  if (!content || typeof content !== 'string') return false
  const hasSchema = content.includes('"schema_version"')
  const hasJsonFence = content.includes('```json') || content.includes('```')
  const hasEntryExit = content.includes('"entry"') || content.includes('"exit"')
  return hasSchema && (hasJsonFence || hasEntryExit)
}

const goStrategies = () => router.push('/strategies')

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
  messages.value.push(tempMsg)
  scrollToBottom()
  typing.value = true
  sending.value = true
  try {
    await sendChat(text)
  } catch (e) {
    typing.value = false
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
})

onUnmounted(() => {
  if (unsubscribe) unsubscribe()
})
</script>

<template>
  <div class="chat-page">
    <header class="chat-header">
      <button class="back-btn" @click="router.push('/dashboard')">← 返回</button>
      <div class="bot-avatar">🤖</div>
      <div class="bot-info">
        <div class="bot-name">智能助手</div>
        <div class="bot-status">在线 · 行情分析中</div>
      </div>
    </header>

    <div class="chat-body" ref="listEl">
      <div v-if="messages.length === 0 && !typing" class="chat-empty">
        <div class="empty-icon">🤖</div>
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
          <div class="bubble">{{ m.content }}</div>
          <div class="time">{{ formatTime(m.createdAt) }}</div>
          <div v-if="m.type !== 'CHAT_USER' && hasStrategyContent(m.content)" class="strategy-action">
            <button class="strategy-btn" @click="goStrategies">查看策略库</button>
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

      <p v-if="error" class="chat-error">{{ error }}</p>
    </div>

    <footer class="chat-input-bar">
      <input
        v-model="input"
        class="chat-input"
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
  height: 100vh;
  background: #f0f2f5;
}
.chat-header {
  background: #1a1a2e;
  color: white;
  padding: 12px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  flex-shrink: 0;
}
.back-btn {
  background: transparent;
  border: 1px solid rgba(255,255,255,0.4);
  color: white;
  padding: 6px 12px;
  border-radius: 4px;
  cursor: pointer;
  font-size: 13px;
}
.bot-avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  background: #1677ff;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}
.bot-info { display: flex; flex-direction: column; }
.bot-name { font-size: 15px; font-weight: 600; }
.bot-status { font-size: 11px; color: #9fa8c0; margin-top: 1px; }

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
  color: #999;
  margin-top: 40px;
}
.empty-icon { font-size: 48px; margin-bottom: 8px; }
.empty-hint { font-size: 12px; margin-top: 4px; color: #bbb; }

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
  background: #1677ff;
  color: white;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 16px;
  flex-shrink: 0;
}
.mine-avatar { background: #fa8c16; font-size: 12px; }

.bubble-wrap { display: flex; flex-direction: column; gap: 3px; }
.bubble {
  padding: 9px 13px;
  font-size: 14px;
  line-height: 1.5;
  word-break: break-word;
  white-space: pre-wrap;
}
.msg-row.theirs .bubble {
  background: white;
  border-radius: 4px 12px 12px 12px;
  color: #333;
  box-shadow: 0 1px 2px rgba(0,0,0,0.06);
}
.msg-row.mine .bubble {
  background: #1677ff;
  border-radius: 12px 4px 12px 12px;
  color: white;
}
.time { font-size: 10px; color: #bbb; }
.msg-row.mine .time { text-align: right; }

.strategy-action { margin-top: 4px; }
.strategy-btn {
  background: #f6ffed;
  color: #389e0d;
  border: 1px solid #b7eb8f;
  border-radius: 4px;
  padding: 3px 10px;
  font-size: 12px;
  cursor: pointer;
}
.strategy-btn:hover { background: #d9f7be; }

.typing-bubble { display: flex; align-items: center; gap: 4px; }
.typing-bubble span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #bbb;
  animation: blink 1.2s infinite;
}
.typing-bubble span:nth-child(2) { animation-delay: 0.2s; }
.typing-bubble span:nth-child(3) { animation-delay: 0.4s; }
@keyframes blink {
  0%, 60%, 100% { opacity: 0.25; }
  30% { opacity: 1; }
}

.chat-error { color: #ff4d4f; font-size: 12px; text-align: center; }

.chat-input-bar {
  display: flex;
  gap: 8px;
  padding: 10px 12px;
  background: white;
  border-top: 1px solid #e8e8e8;
  flex-shrink: 0;
}
.chat-input {
  flex: 1;
  border: 1px solid #d9d9d9;
  border-radius: 18px;
  padding: 9px 14px;
  font-size: 14px;
  outline: none;
}
.chat-input:focus { border-color: #1677ff; }
.send-btn {
  background: #1677ff;
  color: white;
  border: none;
  border-radius: 18px;
  padding: 0 20px;
  font-size: 14px;
  cursor: pointer;
}
.send-btn:disabled { background: #9ec5ff; cursor: not-allowed; }
</style>
