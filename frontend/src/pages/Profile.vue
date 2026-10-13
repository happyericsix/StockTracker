<script setup>
/**
 * 个人中心。逻辑自旧版原样移植：推送第二通道（留空=不改已存 Key）、
 * LLM 用量 14 天迷你柱、THS 绑定/解绑、修改密码校验。
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getProfile, changePassword, updateProfile, testPush } from '../api/user.js'
import { getLlmUsage } from '../api/paper.js'
import { messageBus } from '../composables/messageBus.js'
import QrLogin from '../components/QrLogin.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import { getStatus, unbind, syncFavorites } from '../api/ths.js'

const router = useRouter()
const profile = ref({ username: '' })
const loading = ref(false)

// ---- 同花顺绑定 ----
const thsBound = ref(null)      // null=未知，避免闪一下
const thsLastSync = ref(null)
const thsLastError = ref('')
const showBindQr = ref(false)
const thsMsg = ref('')
const working = ref(false)

const loadThsStatus = async () => {
  try {
    const res = await getStatus()
    const d = res.data
    thsBound.value = !!d.bound
    thsLastSync.value = d.lastSyncAt
    thsLastError.value = d.lastError || ''
  } catch {
    thsBound.value = false
  }
}

const onBindSuccess = async () => {
  thsMsg.value = '绑定成功，正在同步自选股...'
  try {
    const res = await syncFavorites()
    thsMsg.value = `同步完成：新增 ${res.data.added} 只`
  } catch {
    thsMsg.value = '绑定成功，但同步失败，可回首页手动重试'
  }
  await loadThsStatus()
}

const handleUnbind = async () => {
  if (!confirm('确定解绑同花顺账号吗？\n已同步进来的自选股会保留，但以后需重新扫码才能再同步。')) return
  working.value = true
  thsMsg.value = ''
  try {
    await unbind()
    thsMsg.value = '已解绑'
    showBindQr.value = false
    await loadThsStatus()
  } catch (e) {
    thsMsg.value = e.response?.data?.message || '解绑失败，请检查网络后重试'
  } finally {
    working.value = false
  }
}

// 修改密码
const showPasswordForm = ref(false)
const passwordForm = ref({ oldPassword: '', newPassword: '', confirmPassword: '' })
const pwdError = ref('')
const pwdSuccess = ref('')

const fetchProfile = async () => {
  try {
    const res = await getProfile()
    const payload = res.data
    if (payload) {
      profile.value = payload
      loadPushFromProfile()
    }
  } catch {
    // 后端不可达时保持空态
  }
}

const handleChangePassword = async () => {
  pwdError.value = ''
  pwdSuccess.value = ''
  if (passwordForm.value.newPassword !== passwordForm.value.confirmPassword) {
    pwdError.value = '两次输入的密码不一致，请重新填写确认密码'
    return
  }
  if (passwordForm.value.newPassword.length < 6) {
    pwdError.value = '密码长度至少 6 位，请重新填写新密码'
    return
  }
  loading.value = true
  try {
    await changePassword({
      oldPassword: passwordForm.value.oldPassword,
      newPassword: passwordForm.value.newPassword,
    })
    pwdSuccess.value = '密码修改成功'
    passwordForm.value = { oldPassword: '', newPassword: '', confirmPassword: '' }
    showPasswordForm.value = false
  } catch (e) {
    pwdError.value = e.response?.data?.message || '密码修改失败，请检查当前密码后重试'
  } finally {
    loading.value = false
  }
}

const handleLogout = () => {
  // 先断开 SSE，避免旧账号连接在登出后继续推送/换账号后串台
  messageBus.disconnect()
  localStorage.removeItem('token')
  router.push('/login')
}

// ---- 消息推送（Bark / Server酱）：站内消息之外的第二通道 ----
const pushForm = ref({ pushChannel: '', pushTarget: '' })
const pushMsg = ref('')
const pushBusy = ref(false)
const PUSH_CHANNEL_OPTIONS = [
  { value: '', label: '关闭（只用站内消息）' },
  { value: 'bark', label: 'Bark（iOS）' },
  { value: 'serverchan', label: 'Server酱（微信）' },
]

const loadPushFromProfile = () => {
  pushForm.value.pushChannel = profile.value.pushChannel || ''
  pushForm.value.pushTarget = ''
}

const handleSavePush = async () => {
  pushMsg.value = ''
  pushBusy.value = true
  try {
    const payload = { pushChannel: pushForm.value.pushChannel }
    // 留空 = 不改已保存的 key（避免"只是换个通道"把 key 抹掉）
    if (pushForm.value.pushTarget.trim()) payload.pushTarget = pushForm.value.pushTarget.trim()
    const res = await updateProfile(payload)
    profile.value = res.data || profile.value
    loadPushFromProfile()
    pushMsg.value = '推送配置已保存'
  } catch (e) {
    pushMsg.value = e.response?.data?.message || '保存失败，请稍后重试'
  } finally {
    pushBusy.value = false
  }
}

const handleTestPush = async () => {
  pushMsg.value = ''
  if (!pushForm.value.pushChannel || !pushForm.value.pushTarget.trim()) {
    pushMsg.value = '测试前请先选择通道并填写目标'
    return
  }
  pushBusy.value = true
  try {
    const res = await testPush({
      pushChannel: pushForm.value.pushChannel,
      pushTarget: pushForm.value.pushTarget.trim(),
    })
    pushMsg.value = res.data?.sent ? '已发送，去手机上看看收到没' : '发送失败：检查地址/Key 是否抄对'
  } catch (e) {
    pushMsg.value = e.response?.data?.message || '测试请求失败'
  } finally {
    pushBusy.value = false
  }
}

const handleDisablePush = async () => {
  pushMsg.value = ''
  pushBusy.value = true
  try {
    const res = await updateProfile({ pushChannel: '', pushTarget: '' })
    profile.value = res.data || profile.value
    loadPushFromProfile()
    pushMsg.value = '已关闭推送（站内消息不受影响）'
  } catch (e) {
    pushMsg.value = e.response?.data?.message || '操作失败'
  } finally {
    pushBusy.value = false
  }
}

// ---- LLM 用量（整站成本口径）----
const llmUsage = ref(null)
const loadLlmUsage = async () => {
  try {
    const res = await getLlmUsage(30)
    llmUsage.value = res.data || null
  } catch {
    llmUsage.value = null
  }
}
const llmDailySeries = () => {
  const rows = (llmUsage.value?.byDay || []).slice(-14)
  const max = Math.max(1, ...rows.map((r) => Number(r.llmTokens) || 0))
  return rows.map((r) => ({ ...r, heightPct: Math.round(((Number(r.llmTokens) || 0) / max) * 100) }))
}

onMounted(() => {
  fetchProfile()
  loadThsStatus()
  loadLlmUsage()
})
</script>

<template>
  <div>
    <div class="mb-5">
      <h1 class="text-[20px] font-semibold text-ink">个人中心</h1>
    </div>

    <!-- 基本信息 -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <h2 class="mb-3.5 text-[15px] font-semibold text-ink">基本信息</h2>
      <div class="flex items-center justify-between border-b border-border py-2 last:border-b-0">
        <span class="text-[13.5px] text-ink-2">用户名</span>
        <span class="text-[13.5px] font-medium text-ink">{{ profile.username || '-' }}</span>
      </div>
    </section>

    <!-- 消息推送 -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <h2 class="text-[15px] font-semibold text-ink">消息推送</h2>
      <p class="mb-3.5 mt-1 text-[12.5px] leading-relaxed text-ink-3">
        模拟盘成交、预警触发时推送到手机。推送只是第二通道——站内消息永远可用。
      </p>
      <div class="flex flex-col gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-[12px] text-ink-2">通道</span>
          <select
            v-model="pushForm.pushChannel"
            class="h-[34px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none"
          >
            <option v-for="opt in PUSH_CHANNEL_OPTIONS" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
          </select>
        </label>
        <label v-if="pushForm.pushChannel" class="flex flex-col gap-1">
          <span class="text-[12px] text-ink-2">{{ pushForm.pushChannel === 'bark' ? 'Bark 服务器地址（如 https://api.day.app/xxxxx）' : 'Server酱 SendKey（如 SCTxxxxx）' }}</span>
          <input
            v-model.trim="pushForm.pushTarget"
            type="text"
            :placeholder="pushForm.pushChannel === 'bark' ? 'https://api.day.app/你的Key' : 'SCTxxxxxxxxxxxx'"
            autocomplete="off"
            class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
          >
        </label>
        <div class="flex flex-wrap gap-2">
          <Button v-if="pushForm.pushChannel" size="sm" variant="primary" :loading="pushBusy" @click="handleSavePush">保存配置</Button>
          <Button v-if="pushForm.pushChannel" size="sm" :disabled="pushBusy" @click="handleTestPush">发条测试</Button>
          <Button v-if="profile.pushChannel" size="sm" variant="ghost" :disabled="pushBusy" @click="handleDisablePush">关闭推送</Button>
        </div>
        <p v-if="profile.pushChannel && !pushForm.pushChannel" class="text-[12.5px] text-ink-2">
          当前已启用：{{ profile.pushChannel === 'bark' ? 'Bark' : 'Server酱' }}（Key 已保存，不在页面回显）
        </p>
        <p v-if="pushMsg" class="text-[12.5px] text-accent" role="status">{{ pushMsg }}</p>
      </div>
    </section>

    <!-- LLM 用量 -->
    <section v-if="llmUsage && (llmUsage.totals?.usageEvents || 0) > 0" class="mb-4 rounded-xl border border-border bg-surface p-5">
      <h2 class="mb-3 text-[15px] font-semibold text-ink">AI 用量（近 {{ llmUsage.days }} 天）</h2>
      <div class="num mb-3 grid gap-x-6 gap-y-2.5 [grid-template-columns:repeat(auto-fit,minmax(140px,1fr))]">
        <div class="flex flex-col gap-0.5"><span class="text-[12px] text-ink-2">LLM 调用</span><strong class="text-[16px] font-semibold text-ink">{{ llmUsage.totals.llmCalls }}</strong></div>
        <div class="flex flex-col gap-0.5"><span class="text-[12px] text-ink-2">消耗 tokens</span><strong class="text-[16px] font-semibold text-ink">{{ Number(llmUsage.totals.llmTokens).toLocaleString() }}</strong></div>
        <div class="flex flex-col gap-0.5"><span class="text-[12px] text-ink-2">工具调用</span><strong class="text-[16px] font-semibold text-ink">{{ llmUsage.totals.toolCalls }}</strong></div>
      </div>
      <div v-if="llmDailySeries().length" class="flex h-[60px] items-end gap-[3px]" aria-hidden="true">
        <div
          v-for="(d, i) in llmDailySeries()"
          :key="i"
          class="min-w-[6px] flex-1 rounded-t-sm border-t-2 border-accent bg-accent-soft"
          :style="{ height: Math.max(4, d.heightPct) + '%' }"
          :title="`${d.date}：${Number(d.llmTokens).toLocaleString()} tokens`"
        ></div>
      </div>
      <p class="mt-2 text-[12px] leading-relaxed text-ink-3">
        委员会每次开会约 3 万 tokens；预算硬顶由服务端配置（当前 {{ llmUsage.dailyTokenBudget > 0 ? llmUsage.dailyTokenBudget + ' tokens/天' : '未设置' }}）。
      </p>
    </section>

    <!-- 同花顺绑定 -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <h2 class="mb-3 text-[15px] font-semibold text-ink">同花顺账号</h2>

      <template v-if="thsBound === true && !showBindQr">
        <p class="mb-2 flex items-center gap-2 text-[13.5px] font-semibold text-down">
          <span class="h-2 w-2 rounded-full bg-down" aria-hidden="true"></span>已绑定
        </p>
        <p class="mb-3.5 text-[13px] leading-relaxed text-ink-2">
          <span v-if="thsLastSync" class="num">上次同步：{{ String(thsLastSync).replace('T', ' ').slice(0, 19) }}</span>
          <span v-else>尚未同步过自选股</span>
          <span v-if="thsLastError" class="text-up"> · {{ thsLastError }}</span>
        </p>
        <div class="flex flex-wrap gap-2">
          <Button size="sm" :disabled="working" @click="showBindQr = true">重新绑定</Button>
          <Button size="sm" variant="ghost" class="text-up hover:bg-up-soft" :disabled="working" @click="handleUnbind">解绑</Button>
        </div>
      </template>

      <template v-else-if="thsBound === false || showBindQr">
        <p class="mb-3.5 text-[13px] leading-relaxed text-ink-2">
          用手机同花顺 App 扫码绑定，绑定后自动同步自选股，以后也能直接扫码登录。
        </p>
        <QrLogin v-if="showBindQr" mode="bind" @success="onBindSuccess" />
        <div class="mt-3 flex flex-wrap gap-2">
          <Button v-if="!showBindQr" size="sm" variant="primary" @click="showBindQr = true">去绑定</Button>
          <Button v-else size="sm" variant="ghost" @click="showBindQr = false">收起</Button>
        </div>
      </template>

      <p v-else class="text-[13px] text-ink-2">加载中...</p>
      <p v-if="thsMsg" class="mt-3 text-[12.5px] text-accent" role="status">{{ thsMsg }}</p>
    </section>

    <!-- 修改密码 -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <h2 class="mb-3.5 text-[15px] font-semibold text-ink">安全设置</h2>
      <div v-if="!showPasswordForm">
        <Button size="sm" @click="showPasswordForm = true">修改密码</Button>
      </div>
      <form v-else class="flex flex-col gap-3" @submit.prevent="handleChangePassword">
        <label class="sr-only" for="pwd-old">当前密码</label>
        <input id="pwd-old" v-model="passwordForm.oldPassword" type="password" placeholder="当前密码" required
          class="h-[38px] rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]" />
        <label class="sr-only" for="pwd-new">新密码（至少 6 位）</label>
        <input id="pwd-new" v-model="passwordForm.newPassword" type="password" placeholder="新密码（至少6位）" required
          class="h-[38px] rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]" />
        <label class="sr-only" for="pwd-confirm">确认新密码</label>
        <input id="pwd-confirm" v-model="passwordForm.confirmPassword" type="password" placeholder="确认新密码" required
          class="h-[38px] rounded-lg border border-border bg-bg px-3 text-[13px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]" />
        <p v-if="pwdError" class="text-[13px] text-up" role="alert">{{ pwdError }}</p>
        <p v-if="pwdSuccess" class="text-[13px] text-down" role="status">{{ pwdSuccess }}</p>
        <div class="flex justify-end gap-2">
          <Button variant="ghost" @click="showPasswordForm = false">取消</Button>
          <Button type="submit" variant="primary" :loading="loading">确认修改</Button>
        </div>
      </form>
    </section>

    <!-- 退出登录：危险但非破坏性（只清本地 token），用描边而非实心红 -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <Button variant="danger" block @click="handleLogout">退出登录</Button>
    </section>
  </div>
</template>
