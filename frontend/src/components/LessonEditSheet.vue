<script setup>
/**
 * 经验修订面板（记忆白盒 · 方案一 2026-10-16）：
 * 修订 = 落一条取代旧行的新版本（后端保证），旧行永远留在历史里。
 *
 * 边界（与后端 reviseLesson 一致）：
 * 1. taskType 与 scope 只读 —— 分类是复发计数的一半键，分区决定哪条策略能读到它；
 * 2. 留空的字段继承原值，不覆盖（后端按"非空才覆盖"处理）；
 * 3. 保存不弹确认：修订可再修，历史可回看，不需要二次确认的破坏性。
 */
import { ref, watch } from 'vue'
import { X } from 'lucide-vue-next'
import { reviseLesson } from '../api/memory.js'
import Button from './ui/button.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  /** 要修订的经验行（Memory.vue 的 lesson 对象，字段与后端 toMap 一致） */
  lesson: { type: Object, default: null },
})
const emit = defineEmits(['close', 'saved'])

const saving = ref(false)
const error = ref('')
const symptom = ref('')
const reusableRule = ref('')
const resolution = ref('')
const confidence = ref('')

watch(() => props.open, (open) => {
  if (!open) return
  error.value = ''
  // 预填原值：留空 = 继承原值（后端口径），这里展示的就是"改后"的样子
  symptom.value = props.lesson?.symptomRaw || props.lesson?.symptom || ''
  reusableRule.value = props.lesson?.reusableRule || ''
  resolution.value = props.lesson?.resolution || ''
  confidence.value = props.lesson?.confidence ?? ''
})

const save = async () => {
  if (saving.value || !props.lesson) return
  if (!symptom.value.trim()) { error.value = '症状不能为空（它是经验匹配的键）'; return }
  if (!reusableRule.value.trim() && !resolution.value.trim()) {
    error.value = '做法与解决方式至少留一个，否则只是抱怨不是经验'; return
  }
  const payload = {
    symptom: symptom.value.trim(),
    reusableRule: reusableRule.value.trim(),
    resolution: resolution.value.trim(),
  }
  const parsed = Number(confidence.value)
  if (confidence.value !== '' && !Number.isNaN(parsed)) {
    if (parsed < 0 || parsed > 1) { error.value = '置信度取 0 到 1 之间（例如 0.7）'; return }
    payload.confidence = parsed
  }
  saving.value = true
  error.value = ''
  try {
    await reviseLesson(props.lesson.id, payload)
    emit('saved')
    emit('close')
  } catch (e) {
    error.value = e?.response?.data?.message || '修订失败，请稍后重试'
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <Teleport to="body">
    <Transition name="sheet">
      <div
        v-if="open && lesson"
        class="fixed inset-0 z-50 flex max-sm:items-end sm:justify-end"
        :style="{ background: 'var(--overlay)' }"
        @click.self="emit('close')"
      >
        <aside
          class="flex h-[85dvh] w-full flex-col rounded-t-2xl border border-border bg-surface sm:h-dvh sm:w-[min(520px,100%)] sm:rounded-none sm:border-y-0 sm:border-l"
          role="dialog"
          aria-label="修订这条经验"
          style="box-shadow: var(--shadow-pop)"
        >
          <header class="flex items-start justify-between gap-3 border-b border-border px-5 pb-3 pt-4">
            <div>
              <h2 class="text-[16px] font-semibold text-ink">修订这条经验</h2>
              <p class="mt-1 text-[12.5px] text-ink-3">
                保存后生成新版本并取代本条，旧版本保留在历史里（{{ lesson.taskTypeLabel || lesson.taskType }}）
              </p>
            </div>
            <button
              type="button"
              class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg text-ink-2 transition-colors hover:bg-surface-2 hover:text-ink"
              aria-label="关闭"
              @click="emit('close')"
            >
              <X :size="18" :stroke-width="2" aria-hidden="true" />
            </button>
          </header>

          <div class="flex flex-1 flex-col gap-3.5 overflow-y-auto px-5 py-4">
            <label class="flex flex-col gap-1 text-[12.5px] text-ink-2">
              症状（这类问题的表现）
              <input
                v-model="symptom"
                type="text"
                maxlength="200"
                class="h-[34px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
              >
              <em class="not-italic text-[11.5px] leading-relaxed text-ink-3">
                症状是经验的匹配键：改了它，这条经验会挂到新的键下（复发计数重新起算）。
              </em>
            </label>

            <label class="flex flex-col gap-1 text-[12.5px] text-ink-2">
              可复用做法（一句可执行、可验证的话）
              <textarea
                v-model="reusableRule"
                rows="3"
                maxlength="500"
                class="resize-none rounded-lg border border-border bg-bg px-2.5 py-2 text-[13px] leading-relaxed text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
              ></textarea>
              <em class="not-italic text-[11.5px] leading-relaxed text-ink-3">
                是操作方法，不是投资结论（"首仓不超过现金 50%" 可以，"该股要涨" 不行）。
              </em>
            </label>

            <label class="flex flex-col gap-1 text-[12.5px] text-ink-2">
              当时的解决方式
              <textarea
                v-model="resolution"
                rows="2"
                maxlength="1000"
                class="resize-none rounded-lg border border-border bg-bg px-2.5 py-2 text-[13px] leading-relaxed text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
              ></textarea>
            </label>

            <label class="flex flex-col gap-1 text-[12.5px] text-ink-2">
              置信度（0–1，留空继承原值）
              <input
                v-model="confidence"
                type="text"
                inputmode="decimal"
                placeholder="例如 0.7"
                class="num h-[34px] w-28 rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
              >
            </label>

            <p v-if="error" class="text-[12.5px] text-up" role="alert">{{ error }}</p>
          </div>

          <footer class="flex items-center justify-between gap-3 border-t border-border px-5 py-3">
            <p class="text-[11.5px] leading-relaxed text-ink-3">
              人工修订直接生效并继承原审核状态；证据链（来源对话）原样带到新版本。
            </p>
            <div class="flex shrink-0 gap-2">
              <Button variant="ghost" @click="emit('close')">取消</Button>
              <Button variant="primary" :loading="saving" @click="save()">保存修订</Button>
            </div>
          </footer>
        </aside>
      </div>
    </Transition>
  </Teleport>
</template>

<style scoped>
.sheet-enter-active, .sheet-leave-active { transition: opacity 0.18s ease; }
.sheet-enter-active aside, .sheet-leave-active aside { transition: transform 0.22s cubic-bezier(0.2, 0, 0, 1); }
.sheet-enter-from, .sheet-leave-to { opacity: 0; }
@media (min-width: 640px) {
  .sheet-enter-from aside, .sheet-leave-to aside { transform: translateX(30px); }
}
@media (max-width: 639px) {
  .sheet-enter-from aside, .sheet-leave-to aside { transform: translateY(40px); }
}
</style>
