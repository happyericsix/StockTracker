<script setup>
/**
 * 使用说明（分块）：两种模式共用同一份内容（lib/guideContent.js）。
 *
 * - onboarding：首次登录的全屏引导。线性翻页，翻到最后一页才出现
 *   「开始使用」——中间不允许关闭（没有 X、Esc 无效、点遮罩无效），
 *   这是产品要求：第一次进来必须完整过一遍口径与边界。
 * - browse：个人中心的长期入口（「使用说明」）。模态 + 左侧分块目录，
 *   随便跳、随时关——重温不需要仪式感。
 *
 * 可访问性：onboarding 支持 ←/→ 翻页；两个模式都有 role=dialog。
 */
import { ref, computed, watch } from 'vue'
import {
  Sparkles, CandlestickChart, Radar, Bot, BookOpen, TrendingUp, Bell, ShieldCheck,
  ChevronLeft, ChevronRight,
} from 'lucide-vue-next'
import { guideSections } from '../lib/guideContent.js'
import Button from './ui/button.vue'
import Modal from './ui/modal.vue'

const props = defineProps({
  open: { type: Boolean, default: false },
  mode: { type: String, default: 'browse' }, // 'onboarding' | 'browse'
})
const emit = defineEmits(['complete', 'close'])

const icons = {
  Sparkles, CandlestickChart, Radar, Bot, BookOpen, TrendingUp, Bell, ShieldCheck,
}

const current = ref(0)
const isLast = computed(() => current.value >= guideSections.length - 1)
const section = computed(() => guideSections[current.value])

// 每次打开都从第一块开始：引导是"过一遍"，重温默认也从头上手
watch(() => props.open, (value) => { if (value) current.value = 0 })

const go = (delta) => {
  const next = current.value + delta
  if (next >= 0 && next < guideSections.length) current.value = next
}

const selectSection = (index) => { current.value = index }

const finish = () => {
  if (isLast.value) emit('complete')
}

const onboardingKeydown = (e) => {
  if (e.key === 'ArrowRight') go(1)
  else if (e.key === 'ArrowLeft') go(-1)
}

watch(() => props.open, (value) => {
  if (value && props.mode === 'onboarding') {
    document.addEventListener('keydown', onboardingKeydown)
  } else {
    document.removeEventListener('keydown', onboardingKeydown)
  }
})
</script>

<template>
  <!-- ============ 模式一：首次引导（全屏、线性、不可中途关闭） ============ -->
  <Teleport to="body">
    <div
      v-if="open && mode === 'onboarding'"
      class="fixed inset-0 z-[70] flex items-center justify-center p-4 sm:p-6"
      :style="{ background: 'var(--overlay)' }"
      role="dialog"
      aria-modal="true"
      aria-label="使用说明（首次引导）"
    >
      <div
        class="flex max-h-[calc(100dvh-48px)] w-full max-w-[640px] flex-col overflow-hidden rounded-2xl border border-border bg-surface"
        :style="{ boxShadow: 'var(--shadow-pop)' }"
      >
        <!-- 进度条：第 N / 共 M 块 + 线性进度 -->
        <div class="border-b border-border px-6 pb-3 pt-5">
          <div class="mb-2 flex items-center justify-between">
            <p class="text-[12px] text-ink-2">
              使用说明 · 第 {{ current + 1 }} / {{ guideSections.length }} 块
            </p>
            <div class="flex gap-1.5" aria-hidden="true">
              <!-- 进度点只展示、不可点：引导的语义是"翻阅"——想往前走得一步一步按，
                   分块自选跳转留给个人中心的重温模式 -->
              <span
                v-for="(s, i) in guideSections"
                :key="s.id"
                class="h-1.5 rounded-full transition-all duration-200"
                :class="i === current ? 'w-5 bg-accent' : 'w-1.5 bg-surface-2'"
              ></span>
            </div>
          </div>
          <div class="h-0.5 w-full overflow-hidden rounded-full bg-surface-2">
            <div
              class="h-full bg-accent transition-all duration-200"
              :style="{ width: `${((current + 1) / guideSections.length) * 100}%` }"
            ></div>
          </div>
        </div>

        <!-- 内容区 -->
        <div class="flex-1 overflow-y-auto px-6 py-5">
          <div class="mb-1.5 flex items-center gap-2.5">
            <span class="flex h-9 w-9 items-center justify-center rounded-lg bg-accent-soft text-accent">
              <component :is="icons[section.icon]" :size="19" :stroke-width="1.75" aria-hidden="true" />
            </span>
            <div>
              <h2 class="text-[17px] font-semibold leading-6 text-ink">{{ section.title }}</h2>
              <p class="text-[12.5px] text-ink-2">{{ section.subtitle }}</p>
            </div>
          </div>

          <div v-for="block in section.blocks" :key="block.h" class="mt-4">
            <h3 class="mb-1.5 text-[13px] font-semibold text-ink">{{ block.h }}</h3>
            <ul class="space-y-1.5">
              <li
                v-for="item in block.items"
                :key="item"
                class="flex gap-2 text-[13px] leading-relaxed text-ink-2"
              >
                <span class="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-ink-3" aria-hidden="true"></span>
                {{ item }}
              </li>
            </ul>
          </div>
        </div>

        <!-- 底部：翻页（最后一页才出现「开始使用」） -->
        <div class="flex items-center justify-between gap-3 border-t border-border px-6 py-4">
          <Button variant="ghost" :disabled="current === 0" @click="go(-1)">
            <ChevronLeft :size="15" :stroke-width="2" aria-hidden="true" /> 上一块
          </Button>
          <p v-if="!isLast" class="hidden text-[12px] text-ink-2 sm:block">
            翻完全部内容后即可开始使用（可用 ← / → 键翻页）
          </p>
          <Button v-if="!isLast" variant="primary" @click="go(1)">
            下一块 <ChevronRight :size="15" :stroke-width="2" aria-hidden="true" />
          </Button>
          <Button v-else variant="primary" @click="finish">
            我已了解，开始使用
          </Button>
        </div>
      </div>
    </div>
  </Teleport>

  <!-- ============ 模式二：重温（模态、分块自选、随时可关） ============ -->
  <Modal
    :open="open && mode === 'browse'"
    title="使用说明"
    width="min(880px, 100%)"
    @close="emit('close')"
  >
    <div class="flex flex-col gap-4 sm:flex-row sm:gap-5">
      <!-- 分块目录（移动端横向滚动，桌面竖排） -->
      <nav
        class="flex shrink-0 gap-1.5 overflow-x-auto pb-1 sm:w-[176px] sm:flex-col sm:overflow-visible sm:pb-0"
        aria-label="使用说明目录"
      >
        <button
          v-for="(s, i) in guideSections"
          :key="s.id"
          type="button"
          class="flex shrink-0 items-center gap-2 rounded-lg px-2.5 py-2 text-left text-[12.5px] transition-colors"
          :class="i === current
            ? 'bg-surface-2 font-medium text-ink'
            : 'text-ink-2 hover:bg-surface-2 hover:text-ink'"
          :aria-current="i === current ? 'true' : undefined"
          @click="selectSection(i)"
        >
          <component :is="icons[s.icon]" :size="15" :stroke-width="1.75" aria-hidden="true" />
          <span class="truncate">{{ s.title }}</span>
        </button>
      </nav>

      <!-- 内容区 -->
      <div class="min-w-0 flex-1">
        <div class="mb-3 flex items-center gap-2.5">
          <span class="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
            <component :is="icons[section.icon]" :size="17" :stroke-width="1.75" aria-hidden="true" />
          </span>
          <div class="min-w-0">
            <h3 class="text-[15px] font-semibold leading-5 text-ink">{{ section.title }}</h3>
            <p class="truncate text-[12px] text-ink-2">{{ section.subtitle }}</p>
          </div>
        </div>

        <div v-for="block in section.blocks" :key="block.h" class="mb-3.5">
          <h4 class="mb-1.5 text-[13px] font-semibold text-ink">{{ block.h }}</h4>
          <ul class="space-y-1.5">
            <li
              v-for="item in block.items"
              :key="item"
              class="flex gap-2 text-[13px] leading-relaxed text-ink-2"
            >
              <span class="mt-[9px] h-1 w-1 shrink-0 rounded-full bg-ink-3" aria-hidden="true"></span>
              {{ item }}
            </li>
          </ul>
        </div>

        <div class="mt-4 flex items-center justify-between border-t border-border pt-3">
          <Button variant="ghost" size="sm" :disabled="current === 0" @click="go(-1)">
            <ChevronLeft :size="14" :stroke-width="2" aria-hidden="true" /> 上一块
          </Button>
          <p class="text-[11.5px] text-ink-2">{{ current + 1 }} / {{ guideSections.length }}</p>
          <Button variant="ghost" size="sm" :disabled="isLast" @click="go(1)">
            下一块 <ChevronRight :size="14" :stroke-width="2" aria-hidden="true" />
          </Button>
        </div>
      </div>
    </div>
  </Modal>
</template>
