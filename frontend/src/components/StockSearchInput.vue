<script setup>
/**
 * 股票搜索组合框。逻辑自旧版原样移植：
 * - 300ms 防抖 + AbortController 防竞态（快速输入时旧结果不覆盖新结果）
 * - localStorage 历史（15 条），焦点空关键词时展示
 * - 组合框语义：焦点留在输入框，aria-activedescendant 报高亮项
 * - 选中即跳 /chart/:code
 */
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
import { Search } from 'lucide-vue-next'
import { searchStock } from '../api/stock.js'

const STORAGE_KEY = 'stock_search_history'
const MAX_HISTORY = 15

const props = defineProps({ modelValue: { type: String, default: '' } })
const emit = defineEmits(['update:modelValue'])

const router = useRouter()
const keyword = ref(props.modelValue)
const results = ref([])
const recentSearches = ref([])
const showDropdown = ref(false)
const activeIndex = ref(-1)
const loading = ref(false)

let timer = null
let abortController = null
const wrapperRef = ref(null)

// ---- localStorage 历史记录 ----

const loadHistory = () => {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    recentSearches.value = raw ? JSON.parse(raw) : []
  } catch {
    recentSearches.value = []
  }
}

const saveHistory = () => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(recentSearches.value))
}

const addToHistory = (code, name) => {
  if (!code || !name) return
  recentSearches.value = [
    { code, name },
    ...recentSearches.value.filter((item) => item.code !== code),
  ].slice(0, MAX_HISTORY)
  saveHistory()
}

const clearHistory = () => {
  recentSearches.value = []
  localStorage.removeItem(STORAGE_KEY)
  showDropdown.value = false
}

const recordSearch = (code, name) => {
  addToHistory(code, name || code)
}

defineExpose({ recordSearch })

// ---- v-model 同步 ----

watch(() => props.modelValue, (val) => { keyword.value = val })

const onInput = (e) => {
  keyword.value = e.target.value
  emit('update:modelValue', keyword.value)
}

// ---- 搜索防抖 + AbortController 防竞态 ----

watch(keyword, (val) => {
  if (timer) clearTimeout(timer)
  if (abortController) {
    abortController.abort()
    abortController = null
  }
  if (!val || !val.trim()) {
    results.value = []
    showDropdown.value = false
    activeIndex.value = -1
    return
  }
  timer = setTimeout(async () => {
    abortController = new AbortController()
    const current = abortController
    loading.value = true
    try {
      const res = await searchStock(val.trim(), current.signal)
      if (current !== abortController) return
      results.value = res.data?.results || []
      showDropdown.value = true
      activeIndex.value = -1
    } catch (err) {
      if (err?.code === 'ERR_CANCELED' || current !== abortController) return
      results.value = []
    } finally {
      if (current === abortController) {
        loading.value = false
      }
    }
  }, 300)
})

// ---- 选择条目 ----

const selectItem = (item) => {
  keyword.value = item.code
  emit('update:modelValue', item.code)
  addToHistory(item.code, item.name)
  showDropdown.value = false
  router.push('/chart/' + item.code)
}

const selectRecent = (item) => selectItem(item)

// ---- 键盘导航 ----

const onKeydown = (e) => {
  const list = getActiveList()
  if (!showDropdown.value || list.length === 0) return

  if (e.key === 'ArrowDown') {
    e.preventDefault()
    activeIndex.value = (activeIndex.value + 1) % list.length
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    activeIndex.value = activeIndex.value <= 0 ? list.length - 1 : activeIndex.value - 1
  } else if (e.key === 'Enter') {
    e.preventDefault()
    if (activeIndex.value >= 0 && activeIndex.value < list.length) {
      const item = list[activeIndex.value]
      isRecentMode() ? selectRecent(item) : selectItem(item)
    }
  } else if (e.key === 'Escape') {
    showDropdown.value = false
    activeIndex.value = -1
  }
}

const getActiveList = () => (isRecentMode() ? recentSearches.value : results.value)
const isRecentMode = () => !keyword.value || !keyword.value.trim()

const activeDescendantId = computed(() => {
  if (!showDropdown.value || activeIndex.value < 0) return undefined
  const item = getActiveList()[activeIndex.value]
  if (!item) return undefined
  return `${isRecentMode() ? 'search-hist' : 'search-opt'}-${item.code}`
})

// ---- 焦点 ----

const onFocus = () => {
  if (isRecentMode() && recentSearches.value.length > 0) {
    showDropdown.value = true
    activeIndex.value = -1
  } else if (results.value.length > 0) {
    showDropdown.value = true
  }
}

const onBlur = () => {
  setTimeout(() => { showDropdown.value = false }, 150)
}

const onClickOutside = (e) => {
  if (wrapperRef.value && !wrapperRef.value.contains(e.target)) {
    showDropdown.value = false
  }
}

onMounted(() => {
  loadHistory()
  document.addEventListener('click', onClickOutside)
})
onUnmounted(() => {
  document.removeEventListener('click', onClickOutside)
  // 输入后 300ms 内跳走组件：防抖定时器仍会触发并发出一次多余请求
  if (timer) clearTimeout(timer)
  if (abortController) abortController.abort()
})
</script>

<template>
  <div ref="wrapperRef" class="relative flex-1">
    <div class="relative">
      <Search
        :size="15"
        :stroke-width="1.75"
        class="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-ink-3"
        aria-hidden="true"
      />
      <input
        :value="keyword"
        class="h-[34px] w-full rounded-lg border border-border bg-surface pl-9 pr-8 text-[13px] text-ink transition-colors placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
        placeholder="输入股票代码或名称，如 600519、贵州茅台"
        autocomplete="off"
        role="combobox"
        aria-label="搜索股票代码或名称"
        aria-autocomplete="list"
        aria-controls="stock-search-listbox"
        :aria-expanded="showDropdown ? 'true' : 'false'"
        :aria-activedescendant="activeDescendantId"
        @input="onInput"
        @keydown="onKeydown"
        @focus="onFocus"
        @blur="onBlur"
      />
      <span
        v-if="loading"
        class="absolute right-2.5 top-1/2 -translate-y-1/2 text-[13px] text-ink-3"
        aria-hidden="true"
      >⌛</span>
    </div>

    <!-- 下拉浮层：绝对定位在输入框下（壳的内容区无 overflow 裁切） -->
    <div
      v-if="showDropdown"
      class="absolute inset-x-0 top-[calc(100%+4px)] z-40 max-h-[280px] overflow-y-auto rounded-lg border border-border bg-surface py-1"
      style="box-shadow: var(--shadow-pop)"
    >
      <!-- 历史记录 -->
      <template v-if="isRecentMode() && recentSearches.length > 0">
        <div class="flex items-center justify-between px-3 pb-1 pt-1.5 text-[12px]">
          <span id="stock-search-history-title" class="text-ink-3">最近搜索</span>
          <button
            class="rounded px-1 py-0.5 text-accent transition-colors hover:text-accent-hover"
            @mousedown.prevent="clearHistory"
          >清空</button>
        </div>
        <ul id="stock-search-listbox" role="listbox" aria-labelledby="stock-search-history-title">
          <li
            v-for="(item, idx) in recentSearches"
            :id="'search-hist-' + item.code"
            :key="'r' + item.code"
            role="option"
            :aria-selected="idx === activeIndex"
            class="flex cursor-pointer items-center gap-3 px-3 py-2 text-[13px]"
            :class="idx === activeIndex ? 'bg-accent-soft' : 'hover:bg-surface-2'"
            @mousedown.prevent="selectRecent(item)"
          >
            <span class="num w-16 shrink-0 font-medium text-accent">{{ item.code }}</span>
            <span class="truncate text-ink">{{ item.name }}</span>
          </li>
        </ul>
      </template>

      <!-- 搜索结果 -->
      <ul
        v-else-if="!isRecentMode() && results.length > 0"
        id="stock-search-listbox"
        role="listbox"
        aria-label="搜索结果"
      >
        <li
          v-for="(item, idx) in results"
          :id="'search-opt-' + item.code"
          :key="item.code"
          role="option"
          :aria-selected="idx === activeIndex"
          class="flex cursor-pointer items-center gap-3 px-3 py-2 text-[13px]"
          :class="idx === activeIndex ? 'bg-accent-soft' : 'hover:bg-surface-2'"
          @mousedown.prevent="selectItem(item)"
        >
          <span class="num w-16 shrink-0 font-medium text-accent">{{ item.code }}</span>
          <span class="truncate text-ink">{{ item.name }}</span>
        </li>
      </ul>

      <div
        v-else-if="!isRecentMode() && keyword && results.length === 0 && !loading"
        class="px-3 py-3 text-center text-[13px] text-ink-3"
      >
        未找到匹配股票
      </div>
    </div>
  </div>
</template>
