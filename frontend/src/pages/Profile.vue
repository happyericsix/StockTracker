<script setup>
/**
 * 个人中心。逻辑自旧版原样移植：推送第二通道（留空=不改已存 Key）、
 * LLM 用量 14 天迷你柱、THS 绑定/解绑、修改密码校验。
 */
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getProfile, changePassword, updateProfile, testPush, getPushLog,
  getLlmConfig, saveLlmConfig, testLlmConfig, clearLlmConfig } from '../api/user.js'
import { getLlmUsage } from '../api/paper.js'
import { messageBus } from '../composables/messageBus.js'
import QrLogin from '../components/QrLogin.vue'
import UsageGuide from '../components/UsageGuide.vue'
import Badge from '../components/ui/badge.vue'
import Button from '../components/ui/button.vue'
import { getStatus, unbind, syncFavorites } from '../api/ths.js'

const router = useRouter()
const profile = ref({ username: '' })
const loading = ref(false)

// ---- 使用说明（长期入口：分块自选重温，不需要像首登那样翻完） ----
const guideOpen = ref(false)

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
    loadPushLogs()
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

// ---- 最近推送状态：把"推送到底有没有成功"变成可查的事实 ----
const pushLogs = ref([])
const loadPushLogs = async () => {
  try {
    const res = await getPushLog(5)
    pushLogs.value = Array.isArray(res.data) ? res.data : []
  } catch {
    pushLogs.value = []
  }
}
const PUSH_CHANNEL_LABELS = { bark: 'Bark', serverchan: 'Server酱' }
const pushChannelLabel = (channel) => PUSH_CHANNEL_LABELS[channel] || channel || '?'
const pushTimeFmt = (iso) => {
  if (!iso) return ''
  const d = new Date(iso)
  if (isNaN(d.getTime())) return ''
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// ---- 交易费用口径：佣金是用户与券商的约定，档案里一份、全局生效 ----
const commissionInput = ref('')
const commissionMsg = ref('')
const commissionBusy = ref(false)

const handleSaveCommission = async () => {
  commissionMsg.value = ''
  const v = commissionInput.value.trim()
  if (v === '') return handleClearCommission()
  const n = Number(v)
  if (!Number.isFinite(n) || n <= 0 || n > 0.3) {
    commissionMsg.value = '佣金率需在 (0, 0.3] 之间，如 0.025 表示万 2.5'
    return
  }
  commissionBusy.value = true
  try {
    const res = await updateProfile({ commissionPct: n })
    profile.value = res.data || profile.value
    commissionInput.value = ''
    commissionMsg.value = '已保存，之后所有模拟盘结算按这个费率收佣金'
  } catch (e) {
    commissionMsg.value = e.response?.data?.message || '保存失败，请稍后重试'
  } finally {
    commissionBusy.value = false
  }
}

const handleClearCommission = async () => {
  commissionMsg.value = ''
  commissionBusy.value = true
  try {
    const res = await updateProfile({ commissionPct: null })
    profile.value = res.data || profile.value
    commissionInput.value = ''
    commissionMsg.value = '已恢复默认（万 2.5）'
  } catch (e) {
    commissionMsg.value = e.response?.data?.message || '操作失败'
  } finally {
    commissionBusy.value = false
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

// ---- AI 模型 Key（BYOK）：聊天/委员会/盯盘/反思都花这里配置的 Key ----
const llmCfg = ref(null)               // 服务器返回的脱敏状态
const llmKeyMsg = ref('')
const llmKeyBusy = ref(false)
const llmKeyTest = ref(null)           // 最近一次测试结果 [{slot, ok, latencyMs, model, error?}]
// 主槽预设选择：'deepseek' = 官方端点（只填 Key）；'custom' = 任意 OpenAI 兼容端点
const llmPrimaryPreset = ref('deepseek')
const llmForm = ref({
  primary: { apiKey: '', baseUrl: '', model: '', chatPath: '' },
  fallback: { apiKey: '', baseUrl: '', model: '', chatPath: '' },
})
const llmShowFallback = ref(false)

const loadLlmConfig = async () => {
  try {
    const res = await getLlmConfig()
    llmCfg.value = res.data || null
    // 已存自定义主槽时把选择器拨到 custom，预设则回 deepseek
    llmPrimaryPreset.value = llmCfg.value?.primary?.baseUrl ? 'custom' : 'deepseek'
    llmShowFallback.value = !!llmCfg.value?.fallback
  } catch {
    llmCfg.value = null
  }
}

// 组装保存/测试用的表单 payload：Key 留空不带上（后端语义 = 保留已存 Key）
const buildLlmPayload = () => {
  const primary = { ...llmForm.value.primary }
  if (llmPrimaryPreset.value === 'deepseek') {
    primary.baseUrl = ''
    primary.model = ''
    primary.chatPath = ''
  }
  const payload = { primary }
  if (llmShowFallback.value) payload.fallback = { ...llmForm.value.fallback }
  return payload
}

const handleSaveLlmKey = async () => {
  llmKeyMsg.value = ''
  llmKeyBusy.value = true
  try {
    const res = await saveLlmConfig(buildLlmPayload())
    llmCfg.value = res.data || llmCfg.value
    llmForm.value = {
      primary: { apiKey: '', baseUrl: '', model: '', chatPath: '' },
      fallback: { apiKey: '', baseUrl: '', model: '', chatPath: '' },
    }
    llmKeyTest.value = null
    llmKeyMsg.value = 'Key 配置已保存'
  } catch (e) {
    llmKeyMsg.value = e.response?.data?.message || e.businessMessage || '保存失败，请检查填写内容'
  } finally {
    llmKeyBusy.value = false
  }
}

const handleTestLlmKey = async () => {
  llmKeyMsg.value = ''
  llmKeyBusy.value = true
  try {
    // 表单里有输入就测表单值（保存前先验证抄没抄对）；全空则复测已存配置
    const form = buildLlmPayload()
    const hasInput = form.primary.apiKey || form.fallback?.apiKey
      || llmPrimaryPreset.value === 'custom' && (form.primary.baseUrl || form.primary.model)
      || form.fallback && (form.fallback.baseUrl || form.fallback.model)
    const res = await testLlmConfig(hasInput ? form : null)
    llmKeyTest.value = res.data?.results || []
    llmKeyMsg.value = res.data?.allOk ? '测试通过，两个槽位都通了' : '有槽位没通，看下方结果'
    if (!hasInput) await loadLlmConfig()
  } catch (e) {
    llmKeyMsg.value = e.response?.data?.message || e.businessMessage || '测试请求失败'
  } finally {
    llmKeyBusy.value = false
  }
}

const handleClearLlmKey = async () => {
  if (!confirm('确定清除全部 AI Key 配置吗？\n清除后智能助手、交易委员会等 AI 功能会降级，直到重新配置。')) return
  llmKeyBusy.value = true
  try {
    await clearLlmConfig()
    llmCfg.value = null
    llmKeyTest.value = null
    llmKeyMsg.value = '已清除'
    llmShowFallback.value = false
    llmPrimaryPreset.value = 'deepseek'
  } catch (e) {
    llmKeyMsg.value = e.response?.data?.message || '清除失败，请稍后重试'
  } finally {
    llmKeyBusy.value = false
  }
}

const llmSlotTestResult = (slot) => (llmKeyTest.value || []).find((r) => r.slot === slot)
const llmTimeFmt = (iso) => iso ? String(iso).replace('T', ' ').slice(0, 16) : ''

onMounted(() => {
  fetchProfile()
  loadThsStatus()
  loadLlmUsage()
  loadPushLogs()
  loadLlmConfig()
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

        <!-- 最近推送状态：每次真实发出的推送一行（含"发条测试"的尝试）。
             配完通道再没出过声时，这里能看出"推送一直在失败"还是"确实没触发过"。 -->
        <div v-if="pushLogs.length" class="mt-1 border-t border-border pt-3">
          <p class="mb-2 text-[12px] font-medium text-ink-2">最近推送状态</p>
          <div class="flex flex-col gap-1.5">
            <div v-for="row in pushLogs" :key="row.id" class="flex items-baseline gap-2 text-[12.5px]">
              <span class="num shrink-0 text-ink-3">{{ pushTimeFmt(row.createdAt) }}</span>
              <span class="shrink-0 font-medium" :class="row.ok ? 'text-down' : 'text-up'">{{ row.ok ? '成功' : '失败' }}</span>
              <span class="shrink-0 text-ink-2">{{ pushChannelLabel(row.channel) }}</span>
              <span class="min-w-0 flex-1 truncate text-ink" :title="row.body || row.title">{{ row.title }}</span>
              <span v-if="!row.ok && row.error" class="max-w-[45%] shrink-0 truncate text-up" :title="row.error">{{ row.error }}</span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <!-- 交易费用口径 -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <h2 class="text-[15px] font-semibold text-ink">交易费用口径（模拟盘）</h2>
      <p class="mb-3.5 mt-1 text-[12.5px] leading-relaxed text-ink-3">
        印花税、过户费按官方公告的生效日期自动取值（无需设置）；佣金是你和券商的约定——
        填你的真实费率，模拟盘的费用就与实盘一致。留空用默认万 2.5。
      </p>
      <div class="flex flex-col gap-3">
        <label class="flex flex-col gap-1">
          <span class="text-[12px] text-ink-2">我的佣金率（百分数，0.025 = 万 2.5）</span>
          <input
            v-model.trim="commissionInput"
            type="text" inputmode="decimal" autocomplete="off"
            :placeholder="profile.commissionPct != null ? String(profile.commissionPct) : '0.025（默认万 2.5）'"
            class="num h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
          >
        </label>
        <div class="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="primary" :loading="commissionBusy" @click="handleSaveCommission">保存</Button>
          <Button v-if="profile.commissionPct != null" size="sm" variant="ghost" :disabled="commissionBusy" @click="handleClearCommission">恢复默认</Button>
          <span v-if="profile.commissionPct != null" class="text-[12.5px] text-ink-2">当前：{{ profile.commissionPct }}%（万 {{ Math.round(profile.commissionPct * 1000) / 10 }}）</span>
        </div>
        <p v-if="commissionMsg" class="text-[12.5px] text-accent" role="status">{{ commissionMsg }}</p>
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

    <!-- AI 模型 Key（BYOK）：智能助手/交易委员会/盯盘/反思都从这里扣额度 -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <h2 class="text-[15px] font-semibold text-ink">AI 模型 Key（自带额度）</h2>
      <p class="mb-3.5 mt-1 text-[12.5px] leading-relaxed text-ink-3">
        智能助手、交易委员会、盘中盯盘、复盘反思用的都是<b>你自己的</b>大模型额度——
        在 <a class="text-accent underline" href="https://platform.deepseek.com" target="_blank" rel="noopener">DeepSeek 开放平台</a>
        注册并充值即可获得 API Key（新用户有免费额度）。Key 加密存储，不回显、不外传。
      </p>

      <!-- 服务器未启用 Key 保管（LLM_AES_KEY 未配置）：明示而不是让保存悄悄失败 -->
      <p v-if="llmCfg && llmCfg.enabled === false" class="rounded-lg bg-up-soft px-3 py-2 text-[12.5px] text-up" role="alert">
        服务器未启用 Key 保管功能（缺 LLM_AES_KEY 配置），请联系部署者后再配置。
      </p>

      <template v-else>
        <div class="flex flex-col gap-3">
          <!-- 主槽 -->
          <div class="flex flex-col gap-2.5 rounded-lg border border-border bg-bg p-3.5">
            <div class="flex items-center justify-between">
              <span class="text-[12.5px] font-semibold text-ink">主 Key</span>
              <span v-if="llmCfg?.primary" class="text-[11.5px] text-ink-3">
                已保存 {{ llmCfg.primary.maskedKey }}<template v-if="llmCfg.primary.preset">（DeepSeek 官方）</template>
              </span>
            </div>
            <label class="flex flex-col gap-1">
              <span class="text-[12px] text-ink-2">供应商</span>
              <select v-model="llmPrimaryPreset"
                class="h-[34px] rounded-lg border border-border bg-bg px-2.5 text-[13px] text-ink focus:border-accent focus:outline-none">
                <option value="deepseek">DeepSeek 官方（推荐，只填 Key）</option>
                <option value="custom">自定义 OpenAI 兼容端点（硅基流动/Moonshot/智谱…）</option>
              </select>
            </label>
            <label class="flex flex-col gap-1">
              <span class="text-[12px] text-ink-2">API Key{{ llmCfg?.primary ? '（留空 = 不改已存 Key）' : '' }}</span>
              <input v-model.trim="llmForm.primary.apiKey" type="password" autocomplete="new-password"
                :placeholder="llmCfg?.primary ? '已保存，不在页面回显' : 'sk-...'"
                class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]">
            </label>
            <template v-if="llmPrimaryPreset === 'custom'">
              <label class="flex flex-col gap-1">
                <span class="text-[12px] text-ink-2">Base URL（如 https://api.siliconflow.cn）</span>
                <input v-model.trim="llmForm.primary.baseUrl" type="text" autocomplete="off" placeholder="https://api.siliconflow.cn"
                  class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]">
              </label>
              <label class="flex flex-col gap-1">
                <span class="text-[12px] text-ink-2">模型名（该端点提供的，如 Qwen/Qwen2.5-72B-Instruct）</span>
                <input v-model.trim="llmForm.primary.model" type="text" autocomplete="off" placeholder="Qwen/Qwen2.5-72B-Instruct"
                  class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]">
              </label>
              <details class="text-[12px] text-ink-2">
                <summary class="cursor-pointer select-none">高级：接口路径（智谱等端点需要）</summary>
                <label class="mt-1.5 flex flex-col gap-1">
                  <span class="text-[12px] text-ink-3">chat/completions 路径，默认 /v1/chat/completions；智谱配 /chat/completions（Base URL 填到 /api/paas/v4）</span>
                  <input v-model.trim="llmForm.primary.chatPath" type="text" autocomplete="off" placeholder="/v1/chat/completions"
                    class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none">
                </label>
              </details>
            </template>
          </div>

          <!-- 备用槽（可选）：主 Key 限速/过载时自动切过来，恢复后切回 -->
          <div class="rounded-lg border border-border bg-bg p-3.5">
            <label class="flex cursor-pointer items-center gap-2">
              <input type="checkbox" v-model="llmShowFallback" class="accent-[color:var(--accent)]">
              <span class="text-[12.5px] font-semibold text-ink">备用 Key（可选，推荐）</span>
              <span v-if="llmCfg?.fallback" class="text-[11.5px] text-ink-3">已保存 {{ llmCfg.fallback.maskedKey }}</span>
            </label>
            <div v-if="llmShowFallback" class="mt-2.5 flex flex-col gap-2.5">
              <p class="text-[12px] leading-relaxed text-ink-3">
                主 Key 持续限速/过载时自动切到这家（10 分钟冷却，恢复后切回）。
                任意 OpenAI 兼容端点，三样都要填齐。
              </p>
              <label class="flex flex-col gap-1">
                <span class="text-[12px] text-ink-2">Base URL</span>
                <input v-model.trim="llmForm.fallback.baseUrl" type="text" autocomplete="off" placeholder="https://api.siliconflow.cn"
                  class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]">
              </label>
              <label class="flex flex-col gap-1">
                <span class="text-[12px] text-ink-2">API Key{{ llmCfg?.fallback ? '（留空 = 不改已存 Key）' : '' }}</span>
                <input v-model.trim="llmForm.fallback.apiKey" type="password" autocomplete="new-password"
                  :placeholder="llmCfg?.fallback ? '已保存，不在页面回显' : 'sk-...'"
                  class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]">
              </label>
              <label class="flex flex-col gap-1">
                <span class="text-[12px] text-ink-2">模型名</span>
                <input v-model.trim="llmForm.fallback.model" type="text" autocomplete="off" placeholder="Qwen/Qwen2.5-72B-Instruct"
                  class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]">
              </label>
              <details class="text-[12px] text-ink-2">
                <summary class="cursor-pointer select-none">高级：接口路径</summary>
                <label class="mt-1.5 flex flex-col gap-1">
                  <span class="text-[12px] text-ink-3">默认 /v1/chat/completions；智谱配 /chat/completions（Base URL 填到 /api/paas/v4）</span>
                  <input v-model.trim="llmForm.fallback.chatPath" type="text" autocomplete="off" placeholder="/v1/chat/completions"
                    class="h-[34px] w-full rounded-lg border border-border bg-bg px-3 font-mono text-[12.5px] text-ink placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]">
                </label>
              </details>
            </div>
          </div>

          <div class="flex flex-wrap gap-2">
            <Button size="sm" variant="primary" :loading="llmKeyBusy" @click="handleSaveLlmKey">保存</Button>
            <Button size="sm" :disabled="llmKeyBusy" @click="handleTestLlmKey">测试连通性</Button>
            <Button v-if="llmCfg?.configured" size="sm" variant="ghost" class="text-up hover:bg-up-soft" :disabled="llmKeyBusy" @click="handleClearLlmKey">清除全部</Button>
          </div>
          <p v-if="llmKeyMsg" class="text-[12.5px] text-accent" role="status">{{ llmKeyMsg }}</p>

          <!-- 最近一次测试结果：每个槽位一行（通了显示延迟，没通显示原因） -->
          <div v-if="llmKeyTest?.length" class="border-t border-border pt-2.5">
            <div v-for="r in llmKeyTest" :key="r.slot" class="flex items-baseline gap-2 text-[12.5px]">
              <span class="shrink-0 font-medium" :class="r.ok ? 'text-down' : 'text-up'">
                {{ r.slot === 'primary' ? '主' : '备' }}{{ r.ok ? ' ✓ ' + r.latencyMs + 'ms' : ' ✗' }}
              </span>
              <span class="shrink-0 text-ink-3">{{ r.model }}</span>
              <span v-if="!r.ok && r.error" class="min-w-0 flex-1 truncate text-ink-2" :title="r.error">{{ r.error }}</span>
            </div>
          </div>

          <!-- 服务器记录的最近一次测试（复测已存配置时更新） -->
          <p v-if="llmCfg?.lastTestedAt" class="text-[12px] text-ink-3">
            上次测试：{{ llmTimeFmt(llmCfg.lastTestedAt) }}
            {{ llmCfg.lastTestOk ? '· 通过' : '· 未通过' }}
            <template v-if="llmCfg.lastError">（{{ llmCfg.lastError }}）</template>
          </p>
        </div>
      </template>
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

    <!-- 使用说明 / 关于：首登引导的长期入口（分块自选，随时可关） -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <h2 class="mb-1.5 text-[15px] font-semibold text-ink">使用说明 / 关于</h2>
      <p class="mb-3.5 text-[12.5px] text-ink-2">
        分块重温产品的内容与机制：行情与资讯、AI 助手与记忆、策略两种模式、模拟盘口径与边界。
      </p>
      <Button size="sm" @click="guideOpen = true">打开使用说明</Button>
    </section>

    <!-- 退出登录：危险但非破坏性（只清本地 token），用描边而非实心红 -->
    <section class="mb-4 rounded-xl border border-border bg-surface p-5">
      <Button variant="danger" block @click="handleLogout">退出登录</Button>
    </section>

    <UsageGuide :open="guideOpen" mode="browse" @close="guideOpen = false" />
  </div>
</template>
