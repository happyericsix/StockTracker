<script setup>
import { ref, onMounted } from 'vue'
import { RouterView } from 'vue-router'
import { CandlestickChart, X } from 'lucide-vue-next'
import Button from './components/ui/button.vue'

const installEvent = ref(null)
const showInstall = ref(false)

onMounted(() => {
  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault()
    installEvent.value = e
    showInstall.value = true
  })
  window.addEventListener('appinstalled', () => {
    showInstall.value = false
    installEvent.value = null
  })
})

const handleInstall = async () => {
  if (!installEvent.value) return
  installEvent.value.prompt()
  const result = await installEvent.value.userChoice
  if (result.outcome === 'accepted') {
    showInstall.value = false
  }
  installEvent.value = null
}

const dismissInstall = () => {
  showInstall.value = false
}
</script>

<template>
  <RouterView />

  <!-- PWA 安装横幅：浮层卡片（唯一带阴影的层级之一） -->
  <div
    v-if="showInstall"
    class="fixed bottom-[calc(20px+env(safe-area-inset-bottom,0px))] left-1/2 z-50 flex w-[min(420px,calc(100%-32px))] -translate-x-1/2 items-center gap-3 rounded-xl border border-border bg-surface p-3.5"
    style="box-shadow: var(--shadow-pop)"
  >
    <span class="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent-soft text-accent">
      <CandlestickChart :size="22" :stroke-width="1.75" aria-hidden="true" />
    </span>
    <div class="min-w-0 flex-1">
      <p class="text-[13px] font-medium text-ink">安装 Stock Tracker</p>
      <p class="text-[12px] text-ink-2">添加到桌面，快速查看行情</p>
    </div>
    <div class="flex shrink-0 items-center gap-1">
      <Button size="sm" variant="primary" @click="handleInstall">安装</Button>
      <Button size="icon-sm" variant="ghost" aria-label="关闭安装提示" @click="dismissInstall">
        <X :size="15" :stroke-width="2" aria-hidden="true" />
      </Button>
    </div>
  </div>
</template>
