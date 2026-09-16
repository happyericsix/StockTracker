<script setup>
import { ref, computed, watch, onMounted, onUnmounted } from 'vue'
import { useRouter } from 'vue-router'
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
    ...recentSearches.value.filter(item => item.code !== code)
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
  // 取消上一个未完成的请求，避免快速输入时旧结果覆盖新结果
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

const getActiveList = () => isRecentMode() ? recentSearches.value : results.value
const isRecentMode = () => !keyword.value || !keyword.value.trim()

// 组合框语义：焦点始终留在输入框里，用 aria-activedescendant 把当前高亮项
// 告诉屏幕阅读器。这里只算 id，不改动任何键盘行为。
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
onUnmounted(() => document.removeEventListener('click', onClickOutside))
</script>

<template>
  <div ref="wrapperRef" class="search-wrapper">
    <div class="search-input-row">
      <!-- 搜索框视觉上不放标签，用 aria-label 提供可访问名称；
           交互沿用「输入框 + 弹出列表」组合框模式（焦点留在输入框，方向键选项） -->
      <input
        :value="keyword"
        @input="onInput"
        @keydown="onKeydown"
        @focus="onFocus"
        @blur="onBlur"
        role="combobox"
        aria-label="搜索股票代码或名称"
        aria-autocomplete="list"
        aria-controls="stock-search-listbox"
        :aria-expanded="showDropdown ? 'true' : 'false'"
        :aria-activedescendant="activeDescendantId"
        placeholder="输入股票代码或名称，如 600519、贵州茅台"
        autocomplete="off"
      />
      <span v-if="loading" class="loading-icon">&#9203;</span>
    </div>

    <!-- 历史记录 -->
    <div v-if="showDropdown && isRecentMode() && recentSearches.length > 0" class="dropdown">
      <div class="dropdown-header">
        <span class="dropdown-title" id="stock-search-history-title">最近搜索</span>
        <!-- 历史记录没有逐项删除，只有整个「清空」按钮；它自带可见名称 -->
        <button class="clear-btn" @mousedown.prevent="clearHistory">清空</button>
      </div>
      <ul
        id="stock-search-listbox"
        role="listbox"
        aria-labelledby="stock-search-history-title"
      >
        <li
          v-for="(item, idx) in recentSearches"
          :key="'r' + item.code"
          :id="'search-hist-' + item.code"
          role="option"
          :aria-selected="idx === activeIndex"
          :class="{ active: idx === activeIndex }"
          @mousedown.prevent="selectRecent(item)"
        >
          <span class="code num">{{ item.code }}</span>
          <span class="name">{{ item.name }}</span>
        </li>
      </ul>
    </div>

    <!-- 搜索结果 -->
    <ul
      v-if="showDropdown && !isRecentMode() && results.length > 0"
      id="stock-search-listbox"
      class="dropdown"
      role="listbox"
      aria-label="搜索结果"
    >
      <li
        v-for="(item, idx) in results"
        :key="item.code"
        :id="'search-opt-' + item.code"
        role="option"
        :aria-selected="idx === activeIndex"
        :class="{ active: idx === activeIndex }"
        @mousedown.prevent="selectItem(item)"
      >
        <span class="code num">{{ item.code }}</span>
        <span class="name">{{ item.name }}</span>
      </li>
    </ul>

    <div v-if="showDropdown && !isRecentMode() && keyword && results.length === 0 && !loading" class="dropdown empty-dropdown">
      未找到匹配股票
    </div>
  </div>
</template>

<style scoped>
.search-wrapper { position: relative; flex: 1; }
.search-input-row { display: flex; align-items: center; position: relative; }
.search-input-row input {
  width: 100%; padding: 10px 12px; border: 1px solid var(--color-border-control);
  border-radius: var(--radius-sm); font-size: 14px; box-sizing: border-box;
}
/* 焦点：删掉 outline:none 与那圈淡色 box-shadow，
   交给全局 2px 主色焦点环，边框变色只作第二通道 */
.search-input-row input:focus-visible {
  border-color: var(--color-accent);
}
.loading-icon { position: absolute; right: 10px; font-size: 14px; }

.dropdown {
  position: absolute; top: 100%; left: 0; right: 0;
  background: var(--color-bg-surface); border: 1px solid var(--color-border-strong);
  border-top: none;
  border-radius: 0 0 var(--radius-sm) var(--radius-sm);
  max-height: 280px; overflow-y: auto;
  z-index: 1000; list-style: none; margin: 0; padding: 0;
  box-shadow: var(--shadow-1);
}
.dropdown li {
  padding: 8px 12px; cursor: pointer; display: flex;
  gap: 12px; align-items: center; font-size: 14px;
}
/* 历史记录的 li 现在包在真正的 ul 里（合法结构），
   需要显式去掉浏览器默认的项目符号 */
.dropdown ul { list-style: none; }
.dropdown li:hover, .dropdown li.active { background: var(--color-accent-soft); }
.dropdown .code { font-weight: bold; color: var(--color-accent); min-width: 64px; }
.dropdown .name { color: var(--color-text-primary); }

.dropdown-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px 4px; font-size: 12px; color: var(--color-text-muted);
}
.dropdown-header .clear-btn {
  background: none; border: none; color: var(--color-accent);
  cursor: pointer; font-size: 12px; padding: 2px 6px;
}
.dropdown-header .clear-btn:hover { color: var(--color-accent-hover); }

.empty-dropdown { color: var(--color-text-muted); padding: 12px; text-align: center; font-size: 13px; }
</style>