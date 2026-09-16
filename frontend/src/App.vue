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
      <span class="install-icon" aria-hidden="true">📊</span>
      <div>
        <div class="install-title">安装 Stock Tracker</div>
        <div class="install-desc">添加到桌面，快速查看行情</div>
      </div>
    </div>
    <div class="install-actions">
      <button type="button" class="install-btn" @click="handleInstall">安装</button>
      <button type="button" class="dismiss-btn" @click="dismissInstall" aria-label="关闭安装提示">
        <span aria-hidden="true">✕</span>
      </button>
    </div>
  </div>
</template>

<style scoped>
.install-banner {
  position: fixed;
  /* 旧值 bottom: 20px 在带 Home Indicator 的机型上会压到手势条区域 */
  bottom: calc(20px + env(safe-area-inset-bottom, 0px));
  left: 50%;
  transform: translateX(-50%);
  background: var(--color-bg-inverse);
  color: var(--color-text-inverse);
  padding: 14px 20px;
  border-radius: var(--radius-xl);
  box-shadow: var(--shadow-3);
  display: flex;
  align-items: center;
  gap: 16px;
  z-index: 9999;
  /* 旧值 width: calc(100% - 32px) 配 max-width:420px；
     这里用 min() 让窄屏（320px）与宽屏都不会溢出 */
  width: min(420px, calc(100% - 32px));
  animation: slideUp var(--duration-base) var(--ease-out);
}

@keyframes slideUp {
  from {
    transform: translateX(-50%) translateY(12px);
    opacity: 0;
  }
  to {
    transform: translateX(-50%) translateY(0);
    opacity: 1;
  }
}

.install-info {
  display: flex;
  align-items: center;
  gap: 10px;
  flex: 1;
  min-width: 0;
}

.install-icon {
  font-size: 28px;
  flex-shrink: 0;
}

.install-title {
  font-size: 14px;
  font-weight: 600;
}

.install-desc {
  font-size: 12px;
  color: var(--color-text-inverse-muted);
  margin-top: 2px;
}

.install-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-shrink: 0;
}

.install-btn {
  background: var(--color-accent);
  color: var(--color-text-on-accent);
  border: none;
  padding: 8px 18px;
  border-radius: var(--radius-md);
  font-size: 13px;
  white-space: nowrap;
  transition: background-color var(--duration-fast) var(--ease-out);
}

.install-btn:hover {
  background: var(--color-accent-hover);
}

.dismiss-btn {
  background: transparent;
  border: none;
  color: var(--color-text-inverse-muted);
  font-size: 16px;
  padding: 4px 6px;
  /* 旧值只有 4px 内边距，命中区远小于 24×24 的 AA 下限 */
  min-width: 32px;
  min-height: 32px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: var(--radius-sm);
  transition: color var(--duration-fast) var(--ease-out);
}

.dismiss-btn:hover {
  color: var(--color-text-inverse);
}
</style>
