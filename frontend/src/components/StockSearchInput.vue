<script setup>
import { ref, watch, onMounted, onUnmounted } from 'vue'
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
      results.value = res.data?.data?.results || []
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
      <input
        :value="keyword"
        @input="onInput"
        @keydown="onKeydown"
        @focus="onFocus"
        @blur="onBlur"
        placeholder="输入股票代码或名称，如 600519、贵州茅台"
        autocomplete="off"
      />
      <span v-if="loading" class="loading-icon">&#9203;</span>
    </div>

    <!-- 历史记录 -->
    <div v-if="showDropdown && isRecentMode() && recentSearches.length > 0" class="dropdown">
      <div class="dropdown-header">
        <span class="dropdown-title">最近搜索</span>
        <button class="clear-btn" @mousedown.prevent="clearHistory">清空</button>
      </div>
      <li
        v-for="(item, idx) in recentSearches"
        :key="'r' + item.code"
        :class="{ active: idx === activeIndex }"
        @mousedown.prevent="selectRecent(item)"
      >
        <span class="code">{{ item.code }}</span>
        <span class="name">{{ item.name }}</span>
      </li>
    </div>

    <!-- 搜索结果 -->
    <ul v-if="showDropdown && !isRecentMode() && results.length > 0" class="dropdown">
      <li
        v-for="(item, idx) in results"
        :key="item.code"
        :class="{ active: idx === activeIndex }"
        @mousedown.prevent="selectItem(item)"
      >
        <span class="code">{{ item.code }}</span>
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
  width: 100%; padding: 10px 12px; border: 1px solid #d9d9d9;
  border-radius: 4px; font-size: 14px; box-sizing: border-box;
}
.search-input-row input:focus {
  outline: none; border-color: #4096ff;
  box-shadow: 0 0 0 2px rgba(24, 144, 255, 0.2);
}
.loading-icon { position: absolute; right: 10px; font-size: 14px; }

.dropdown {
  position: absolute; top: 100%; left: 0; right: 0;
  background: #fff; border: 1px solid #d9d9d9; border-top: none;
  border-radius: 0 0 4px 4px; max-height: 280px; overflow-y: auto;
  z-index: 1000; list-style: none; margin: 0; padding: 0;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
}
.dropdown li {
  padding: 8px 12px; cursor: pointer; display: flex;
  gap: 12px; align-items: center; font-size: 14px;
}
.dropdown li:hover, .dropdown li.active { background: #e6f4ff; }
.dropdown .code { font-weight: bold; color: #1677ff; min-width: 64px; }
.dropdown .name { color: #333; }

.dropdown-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 8px 12px 4px; font-size: 12px; color: #999;
}
.dropdown-header .clear-btn {
  background: none; border: none; color: #1677ff;
  cursor: pointer; font-size: 12px; padding: 2px 6px;
}
.dropdown-header .clear-btn:hover { color: #4096ff; }

.empty-dropdown { color: #999; padding: 12px; text-align: center; font-size: 13px; }
</style>