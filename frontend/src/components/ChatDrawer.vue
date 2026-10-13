<script setup>
/**
 * 聚焦聊天抽屉（agent 互通 P2）：从资讯卡片"问 AI"打开，围绕一条新闻对话。
 * 逻辑自旧版原样移植：乐观气泡与 SSE 回显按 id 合并去重、只在抽屉开着时收消息、
 * 聚焦期间每条消息都带 focus（后端无状态）。
 *
 * 形态：手机底部 bottom-sheet（88dvh），桌面右侧滑入 420px。
 * 与助手页同一个聊天后端，抽屉只是"带着上下文开口"的入口。
 */
import { ref, nextTick, watch, onUnmounted } from 'vue'
import { Bot, X } from 'lucide-vue-next'
import { sendChat } from '../api/messages.js'
import { renderMarkdown } from '../utils/markdown.js'
import { messageBus } from '../composables/messageBus.js'
import Button from './ui/button.vue'

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
  // 用户消息：SSE 回显与乐观气泡是同一条 —— 换 id 而不是新增，否则显示两遍
  if (msg.type === 'CHAT_USER') {
    const lastLocal = [...messages.value].reverse()
      .find((m) => String(m.id).startsWith('local-') && m.content === msg.content)
    if (lastLocal) {
      lastLocal.id = msg.id
      seenIds.add(msg.id)
      return
    }
  }
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
  } catch {
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
      <div
        v-if="open"
        class="fixed inset-0 z-50 flex items-end"
        :style="{ background: 'var(--overlay)' }"
        @click.self="emit('close')"
      >
        <aside
          class="relative flex h-[88dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-border bg-surface pb-[env(safe-area-inset-bottom)] sm:ml-auto sm:h-dvh sm:w-[420px] sm:rounded-none sm:border-t-0"
          role="dialog"
          aria-label="聚焦这条新闻提问"
        >
          <header class="relative border-b border-border px-4 pb-2.5 pt-3.5">
            <p class="mb-1 flex items-center gap-2">
              <span class="rounded-full border border-accent px-2 py-px text-[11.5px] text-accent">正在讨论这条新闻</span>
              <span
                v-if="focusEvent?.direction"
                class="rounded-full px-2 py-px text-[11.5px] font-semibold"
                :class="focusEvent.direction === '利好' ? 'bg-up-soft text-up' : focusEvent.direction === '利空' ? 'bg-down-soft text-down' : 'bg-surface-2 text-ink-2'"
              >{{ focusEvent.direction }}</span>
            </p>
            <p class="line-clamp-2 pr-10 text-[14px] font-medium leading-relaxed text-ink">
              {{ focusEvent?.title || '新闻聚焦' }}
            </p>
            <button
              type="button"
              class="absolute right-2.5 top-2.5 flex h-8 w-8 items-center justify-center rounded-lg text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              aria-label="关闭"
              @click="emit('close')"
            >
              <X :size="17" :stroke-width="2" aria-hidden="true" />
            </button>
          </header>

          <div ref="listEl" class="flex flex-1 flex-col gap-2.5 overflow-y-auto overscroll-contain p-3.5">
            <div v-if="messages.length === 0 && !typing" class="px-1 py-3">
              <p class="mb-2.5 text-[13px] text-ink-2">围绕这条新闻直接提问，AI 会结合行情与相关专题回答。</p>
              <div class="flex flex-col gap-2">
                <button
                  v-for="q in suggestions(focusEvent)"
                  :key="q"
                  type="button"
                  class="rounded-lg border border-border bg-surface-2 px-3 py-2.5 text-left text-[13px] text-ink transition-colors hover:border-accent"
                  @click="send(q)"
                >{{ q }}</button>
              </div>
            </div>

            <template v-for="m in messages" :key="m.id">
              <div class="flex items-start gap-2" :class="m.type === 'CHAT_USER' ? 'justify-end' : ''">
                <span v-if="m.type !== 'CHAT_USER'" class="mt-0.5 flex h-6.5 w-6.5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-ink-3">
                  <Bot :size="15" :stroke-width="1.75" aria-hidden="true" />
                </span>
                <!-- AI 回复走 markdown 渲染（与助手页同一渲染器）；用户气泡保持纯文本 -->
                <div
                  v-if="m.type !== 'CHAT_USER'"
                  class="md-body max-w-[82%] whitespace-normal rounded-xl rounded-tl-sm bg-surface-2 px-3 py-2 text-[13.5px] leading-relaxed text-ink"
                  v-html="renderMarkdown(m.content)"
                ></div>
                <div
                  v-else
                  class="max-w-[82%] whitespace-pre-wrap break-words rounded-xl rounded-tr-sm bg-accent-soft px-3 py-2 text-[13.5px] leading-relaxed text-accent"
                >{{ m.content }}</div>
              </div>
            </template>

            <div v-if="typing" class="flex items-start gap-2">
              <span class="mt-0.5 flex h-6.5 w-6.5 shrink-0 items-center justify-center rounded-full bg-surface-2 text-ink-3">
                <Bot :size="15" :stroke-width="1.75" aria-hidden="true" />
              </span>
              <div class="flex items-center gap-1 rounded-xl rounded-tl-sm bg-surface-2 px-3.5 py-3">
                <span class="typing-dot"></span><span class="typing-dot" style="animation-delay: 0.2s"></span><span class="typing-dot" style="animation-delay: 0.4s"></span>
              </div>
            </div>
            <p v-if="error" class="text-[12.5px] text-up" role="alert">{{ error }}</p>
          </div>

          <footer class="flex gap-2 border-t border-border px-3 pb-1 pt-2.5">
            <input
              v-model="input"
              type="text"
              placeholder="问问这条新闻…"
              maxlength="500"
              enterkeyhint="send"
              class="h-[38px] min-w-0 flex-1 rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
              @keydown.enter="onEnter"
            >
            <Button variant="primary" :disabled="sending || !input.trim()" @click="send()">
              {{ sending ? '…' : '发送' }}
            </Button>
          </footer>
          <p class="px-3.5 pb-2.5 pt-1 text-[11px] text-ink-3">AI 回复仅供参考，不构成投资建议</p>
        </aside>
      </div>
    </Transition>
  </Teleport>
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

/* 进出场：手机从底部滑入，桌面从右侧滑入 */
.drawer-enter-active, .drawer-leave-active { transition: opacity 0.2s ease; }
.drawer-enter-active aside, .drawer-leave-active aside { transition: transform 0.24s cubic-bezier(0.2, 0, 0, 1); }
.drawer-enter-from, .drawer-leave-to { opacity: 0; }
.drawer-enter-from aside, .drawer-leave-to aside { transform: translateY(100%); }
@media (min-width: 640px) {
  .drawer-enter-from aside, .drawer-leave-to aside { transform: translateX(100%); }
}
</style>
