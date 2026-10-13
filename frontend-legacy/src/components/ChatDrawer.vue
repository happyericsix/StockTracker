<script setup>
/**
 * 聚焦聊天抽屉（agent 互通 P2）：从资讯卡片"问 AI"打开，围绕一条新闻对话。
 *
 * 形态（手机优先）：
 * - 手机（<640px）：底部 bottom-sheet（88dvh、圆角顶、safe-area 底距），拇指可达；
 * - 桌面：右侧滑入 420px 侧栏。
 *
 * 与 Assistant 页的关系：**同一个聊天后端**（/chat/send + messageBus SSE 推送回复），
 * 但抽屉只显示本次聚焦会话内（打开之后）的消息，不载全历史 —— 用户在抽屉里
 * 的对话回到助手页也看得到（同一张 message 表），抽屉只是"带着上下文开口"的入口。
 * 后端无状态：聚焦期间每条消息都带 focus，关抽屉即退出聚焦。
 */
import { ref, nextTick, watch, onUnmounted } from 'vue'
import { sendChat } from '../api/messages.js'
import { renderMarkdown } from '../utils/markdown.js'
import { messageBus } from '../composables/messageBus.js'
import AppIcon from './AppIcon.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  /** 聚焦的新闻事件（NewsEventResponse 子集：id/title/direction 必有） */
  focusEvent: { type: Object, default: null },
})
const emit = defineEmits(['close'])

const messages = ref([])
const input = ref('')
const typing = ref(false)
const sending = ref(false)
const error = ref('')
const listEl = ref(null)
let unsubscribe = null
let seenIds = new Set()

const suggestions = (event) => {
  const base = [
    '这条新闻对该股意味着什么？',
    '这条消息可信吗？有什么风险点？',
    '这件事的后续进展如何？',
  ]
  if (event?.symbol) base.splice(1, 0, `结合行情看看${event.name || event.symbol}现在什么状态？`)
  return base.slice(0, 3)
}

const scrollToBottom = async () => {
  await nextTick()
  if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
}

const onBusMessage = (msg) => {
  if (!props.open || !msg || !msg.id) return
  if (msg.type !== 'CHAT_BOT' && msg.type !== 'CHAT_USER') return
  if (seenIds.has(msg.id)) return
  // 用户消息：SSE 回显与发送时的乐观气泡是同一条 —— 把乐观气泡的临时 id
  // 换成数据库 id（不新增），否则一句话会显示两遍。
  if (msg.type === 'CHAT_USER') {
    const lastLocal = [...messages.value].reverse()
      .find((m) => String(m.id).startsWith('local-') && m.content === msg.content)
    if (lastLocal) {
      lastLocal.id = msg.id
      seenIds.add(msg.id)
      return
    }
  }
  // 只显示聚焦会话开始后到达的消息（历史对话在助手页看）
  seenIds.add(msg.id)
  messages.value.push(msg)
  if (msg.type === 'CHAT_BOT') typing.value = false
  scrollToBottom()
}

watch(() => props.open, (open) => {
  if (open) {
    messages.value = []
    seenIds = new Set()
    error.value = ''
    typing.value = false
    unsubscribe = messageBus.subscribe(onBusMessage)
    messageBus.connect()
  } else if (unsubscribe) {
    unsubscribe()
    unsubscribe = null
    typing.value = false
  }
})

onUnmounted(() => { if (unsubscribe) unsubscribe() })

const send = async (preset) => {
  const text = String(preset || input.value || '').trim()
  if (!text || sending.value) return
  error.value = ''
  input.value = ''
  // 本地乐观气泡：SSE 回显（数据库 id）到达时由 seenIds 去重合并显示
  const local = { id: 'local-' + Date.now(), type: 'CHAT_USER', content: text,
                  createdAt: new Date().toISOString() }
  seenIds.add(local.id)
  messages.value.push(local)
  typing.value = true
  sending.value = true
  scrollToBottom()
  try {
    await sendChat(text, props.focusEvent
      ? { type: 'news_event', eventId: props.focusEvent.id } : null)
  } catch (e) {
    typing.value = false
    error.value = '发送失败，请稍后重试'
  } finally {
    sending.value = false
  }
}

const onEnter = (e) => {
  if (!e.isComposing) send()
}
</script>

<template>
  <Teleport to="body">
    <Transition name="drawer">
      <div v-if="open" class="drawer-mask" @click.self="emit('close')">
        <aside class="chat-drawer" role="dialog" aria-label="聚焦这条新闻提问">
          <header class="drawer-head">
            <div class="focus-meta">
              <span class="focus-tag">正在讨论这条新闻</span>
              <span v-if="focusEvent?.direction" class="direction" :class="focusEvent.direction === '利好' ? 'bull' : focusEvent.direction === '利空' ? 'bear' : 'flat'">{{ focusEvent.direction }}</span>
            </div>
            <p class="focus-title">{{ focusEvent?.title || '新闻聚焦' }}</p>
            <button type="button" class="close-btn" aria-label="关闭" @click="emit('close')">
              <AppIcon name="x" :size="18" :stroke-width="2" />
            </button>
          </header>

          <div class="drawer-body" ref="listEl">
            <div v-if="messages.length === 0 && !typing" class="drawer-empty">
              <p>围绕这条新闻直接提问，AI 会结合行情与相关专题回答。</p>
              <div class="suggestions">
                <button
                  v-for="q in suggestions(focusEvent)"
                  :key="q"
                  type="button"
                  class="suggestion-chip"
                  @click="send(q)"
                >{{ q }}</button>
              </div>
            </div>

            <template v-for="m in messages" :key="m.id">
              <div class="msg-row" :class="m.type === 'CHAT_USER' ? 'mine' : 'theirs'">
                <div v-if="m.type !== 'CHAT_USER'" class="avatar"><AppIcon name="bot" :size="16" :stroke-width="2" /></div>
                <!-- AI 回复走 markdown 渲染（与助手页同一渲染器）：模型的回复带
                     粗体/列表/表格，纯文本显示会是一堆 ** 和竖线（2026-10-10 反馈）。
                     用户气泡保持纯文本：自己打的字不该被当 HTML 处理。 -->
                <div v-if="m.type !== 'CHAT_USER'" class="bubble markdown-body" v-html="renderMarkdown(m.content)"></div>
                <div v-else class="bubble">{{ m.content }}</div>
              </div>
            </template>

            <div v-if="typing" class="msg-row theirs">
              <div class="avatar"><AppIcon name="bot" :size="16" :stroke-width="2" /></div>
              <div class="bubble typing-bubble"><span></span><span></span><span></span></div>
            </div>
            <p v-if="error" class="drawer-error" role="alert">{{ error }}</p>
          </div>

          <footer class="drawer-input">
            <input
              v-model="input"
              type="text"
              placeholder="问问这条新闻…"
              maxlength="500"
              enterkeyhint="send"
              @keydown.enter="onEnter"
            >
            <button type="button" class="send-btn" :disabled="sending || !input.trim()" @click="send()">
              {{ sending ? '…' : '发送' }}
            </button>
          </footer>
          <p class="drawer-notice">AI 回复仅供参考，不构成投资建议</p>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.drawer-mask {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  z-index: 60;
  display: flex;
  align-items: flex-end;          /* 手机：贴底 */
}
.chat-drawer {
  position: relative;
  display: flex;
  flex-direction: column;
  width: 100%;
  height: 88dvh;
  background: var(--color-bg-surface);
  border-radius: 16px 16px 0 0;
  box-shadow: 0 -8px 32px rgba(0, 0, 0, 0.18);
  padding-bottom: env(safe-area-inset-bottom);
  overflow: hidden;
}
/* 桌面：右侧滑入侧栏 */
@media (min-width: 640px) {
  .drawer-mask { align-items: stretch; justify-content: flex-end; }
  .chat-drawer {
    width: 420px;
    height: 100dvh;
    border-radius: 0;
    box-shadow: -8px 0 32px rgba(0, 0, 0, 0.18);
  }
}
.drawer-head {
  position: relative;
  padding: 14px 16px 10px;
  border-bottom: 1px solid var(--color-border);
}
.focus-meta { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.focus-tag {
  font-size: 11.5px;
  color: var(--color-accent);
  border: 1px solid var(--color-accent);
  border-radius: 999px;
  padding: 1px 8px;
}
.direction { font-size: 11.5px; font-weight: 600; }
.direction.bull { color: var(--color-up, #d33); }
.direction.bear { color: var(--color-down, #2a2); }
.direction.flat { color: var(--color-text-muted); }
.focus-title {
  margin: 0;
  padding-right: 40px;
  font: var(--font-heading);
  font-size: 14px;
  line-height: 1.5;
  color: var(--color-text-primary);
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.close-btn {
  position: absolute;
  top: 10px;
  right: 10px;
  padding: 6px;
  border: none;
  border-radius: 8px;
  background: transparent;
  color: var(--color-text-muted);
  cursor: pointer;
}
.close-btn:hover { background: var(--color-bg-subtle); color: var(--color-text-primary); }

.drawer-body {
  flex: 1;
  overflow-y: auto;
  -webkit-overflow-scrolling: touch;
  padding: 12px 14px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.drawer-empty { padding: 12px 4px; }
.drawer-empty p { margin: 0 0 10px; font-size: 13px; color: var(--color-text-secondary); }
.suggestions { display: flex; flex-direction: column; gap: 8px; }
.suggestion-chip {
  text-align: left;
  padding: 9px 12px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-bg-subtle);
  color: var(--color-text-primary);
  font-size: 13px;
  cursor: pointer;
}
.suggestion-chip:hover { border-color: var(--color-accent); }

.msg-row { display: flex; gap: 8px; align-items: flex-start; }
.msg-row.mine { justify-content: flex-end; }
.msg-row .bubble {
  max-width: 82%;
  padding: 8px 12px;
  border-radius: 12px;
  font-size: 13.5px;
  line-height: 1.6;
  white-space: pre-wrap;
  word-break: break-word;
}
.msg-row.theirs .bubble { background: var(--color-bg-subtle); color: var(--color-text-primary); border-top-left-radius: 4px; }
.msg-row.mine .bubble { background: var(--color-accent-soft); color: var(--color-accent); border-top-right-radius: 4px; }

/* AI 气泡的 markdown 排版（与助手页同款，字号按抽屉收窄一档） */
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
.bubble.markdown-body :deep(h1) { font-size: 16px; }
.bubble.markdown-body :deep(h2) { font-size: 15px; }
.bubble.markdown-body :deep(h3),
.bubble.markdown-body :deep(h4),
.bubble.markdown-body :deep(h5),
.bubble.markdown-body :deep(h6) { font-size: 14px; }
.bubble.markdown-body :deep(ul) { margin: 4px 0; padding-left: 18px; }
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
.bubble.markdown-body :deep(pre code) { background: transparent; padding: 0; }
.bubble.markdown-body :deep(hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 8px 0;
}
/* 表格：手机抽屉窄，横向可滚而不是把气泡撑爆 */
.bubble.markdown-body :deep(.md-table-wrap) { overflow-x: auto; margin: 6px 0; }
.bubble.markdown-body :deep(table) { border-collapse: collapse; font-size: 12.5px; }
.bubble.markdown-body :deep(th),
.bubble.markdown-body :deep(td) {
  border: 1px solid var(--color-border);
  padding: 3px 7px;
  text-align: left;
  white-space: nowrap;
}
.bubble.markdown-body :deep(th) { background: var(--color-bg-subtle); font-weight: 600; }
.avatar {
  flex: none;
  width: 26px;
  height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: var(--color-bg-subtle);
  color: var(--color-text-muted);
}
.typing-bubble { display: flex; gap: 4px; align-items: center; }
.typing-bubble span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--color-text-muted);
  animation: drawer-typing 1.2s infinite ease-in-out;
}
.typing-bubble span:nth-child(2) { animation-delay: 0.2s; }
.typing-bubble span:nth-child(3) { animation-delay: 0.4s; }
@keyframes drawer-typing {
  0%, 60%, 100% { opacity: 0.3; transform: translateY(0); }
  30% { opacity: 1; transform: translateY(-3px); }
}
.drawer-error { margin: 0; font-size: 12.5px; color: var(--color-danger, #c33); }

.drawer-input {
  display: flex;
  gap: 8px;
  padding: 10px 12px 4px;
  border-top: 1px solid var(--color-border);
}
.drawer-input input {
  flex: 1;
  min-width: 0;
  padding: 9px 12px;
  border: 1px solid var(--color-border-strong);
  border-radius: 10px;
  background: var(--color-bg);
  color: var(--color-text-primary);
  font-size: 14px;
}
.drawer-input input:focus { outline: none; border-color: var(--color-accent); }
.send-btn {
  flex: none;
  padding: 0 16px;
  border: none;
  border-radius: 10px;
  background: var(--color-accent);
  color: #fff;
  font-size: 14px;
  cursor: pointer;
}
.send-btn:disabled { opacity: 0.5; cursor: default; }
.drawer-notice { margin: 0; padding: 4px 14px 10px; font-size: 11px; color: var(--color-text-muted); }

/* 进出场：手机从底部滑入，桌面从右侧滑入 */
.drawer-enter-active, .drawer-leave-active { transition: opacity 0.2s ease; }
.drawer-enter-active .chat-drawer, .drawer-leave-active .chat-drawer { transition: transform 0.25s var(--ease-out, ease-out); }
.drawer-enter-from, .drawer-leave-to { opacity: 0; }
.drawer-enter-from .chat-drawer, .drawer-leave-to .chat-drawer { transform: translateY(100%); }
@media (min-width: 640px) {
  .drawer-enter-from .chat-drawer, .drawer-leave-to .chat-drawer { transform: translateX(100%); }
}
</style>
