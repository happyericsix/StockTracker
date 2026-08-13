<script setup>
import { ref, onMounted } from 'vue'
import { RouterView } from 'vue-router'

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
  
  <!-- PWA 安装横幅 -->
  <div v-if="showInstall" class="install-banner">
    <div class="install-info">
      <span class="install-icon">📊</span>
      <div>
        <div class="install-title">安装 Stock Tracker</div>
        <div class="install-desc">添加到桌面，快速查看行情</div>
      </div>
    </div>
    <div class="install-actions">
      <button class="install-btn" @click="handleInstall">安装</button>
      <button class="dismiss-btn" @click="dismissInstall">✕</button>
    </div>
  </div>
</template>

<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
</style>

<style scoped>
.install-banner {
  position: fixed;
  bottom: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: #1a1a2e;
  color: white;
  padding: 14px 20px;
  border-radius: 12px;
  box-shadow: 0 4px 20px rgba(0,0,0,0.35);
  display: flex;
  align-items: center;
  gap: 16px;
  z-index: 9999;
  max-width: 420px;
  width: calc(100% - 32px);
  animation: slideUp 0.3s ease;
}
@keyframes slideUp {
  from { transform: translateX(-50%) translateY(20px); opacity: 0; }
  to { transform: translateX(-50%) translateY(0); opacity: 1; }
}
.install-info { display: flex; align-items: center; gap: 10px; flex: 1; }
.install-icon { font-size: 28px; }
.install-title { font-size: 14px; font-weight: 600; }
.install-desc { font-size: 12px; color: #aaa; margin-top: 2px; }
.install-actions { display: flex; align-items: center; gap: 8px; }
.install-btn { background: #1677ff; color: white; border: none; padding: 8px 18px; border-radius: 6px; font-size: 13px; cursor: pointer; white-space: nowrap; }
.install-btn:hover { background: #4096ff; }
.dismiss-btn { background: transparent; border: none; color: #888; font-size: 16px; cursor: pointer; padding: 4px; }
.dismiss-btn:hover { color: white; }
</style>
