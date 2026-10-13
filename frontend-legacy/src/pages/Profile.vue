<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getProfile, changePassword, updateProfile, testPush } from '../api/user.js'
import { getLlmUsage } from '../api/paper.js'
import { messageBus } from '../composables/messageBus.js'
import QrLogin from '../components/QrLogin.vue'
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
  } catch (e) {
    thsBound.value = false
  }
}

const onBindSuccess = async () => {
  thsMsg.value = '绑定成功，正在同步自选股...'
  try {
    const res = await syncFavorites()
    thsMsg.value = `同步完成：新增 ${res.data.added} 只`
  } catch (e) {
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
    // 响应形状由 api/request.js 的拦截器统一处理（Result 信封会被剥掉），
    // 所以这里直接读 res.data，不必再判断后端属于哪一种形状。
    const payload = res.data
    if (payload) {
      profile.value = payload
      loadPushFromProfile()
    }
  } catch (e) {
    // 后端不可达时保持空态：全站没有任何地方写入过 user_profile 这个 key，
    // 旧的 localStorage 兜底分支永远走不到，已删。
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
      newPassword: passwordForm.value.newPassword
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

// ---- 消息推送（Bark / Server酱）----
// 推送是旁路：站内消息永远在，这里只决定"手机上响不响"。
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
    llmUsage.value = null // 拿不到就隐藏，成本卡片不是必选项
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
  <div class="page">
    <div class="page-head">
      <h1>个人中心</h1>
    </div>

      <!-- 基本信息 -->
      <section class="card">
        <h2>基本信息</h2>
        <div class="info-grid">
          <div class="info-item">
            <span class="label">用户名</span>
            <span class="value">{{ profile.username || '-' }}</span>
          </div>
        </div>
      </section>

      <!-- 消息推送：站内消息之外的"手机能响一下"通道 -->
      <section class="card">
        <h2>消息推送</h2>
        <p class="push-hint">
          模拟盘成交、预警触发时推送到手机。推送只是第二通道——站内消息永远可用。
        </p>
        <div class="push-form">
          <label class="push-field">
            <span>通道</span>
            <select v-model="pushForm.pushChannel">
              <option v-for="opt in PUSH_CHANNEL_OPTIONS" :key="opt.value" :value="opt.value">
                {{ opt.label }}
              </option>
            </select>
          </label>
          <label v-if="pushForm.pushChannel" class="push-field wide">
            <span>{{ pushForm.pushChannel === 'bark' ? 'Bark 服务器地址（如 https://api.day.app/xxxxx）' : 'Server酱 SendKey（如 SCTxxxxx）' }}</span>
            <input
              v-model.trim="pushForm.pushTarget"
              type="text"
              :placeholder="pushForm.pushChannel === 'bark' ? 'https://api.day.app/你的Key' : 'SCTxxxxxxxxxxxx'"
              autocomplete="off"
            >
          </label>
          <div class="push-actions">
            <button
              v-if="pushForm.pushChannel"
              type="button"
              class="primary-btn"
              :disabled="pushBusy"
              @click="handleSavePush"
            >保存配置</button>
            <button
              v-if="pushForm.pushChannel"
              type="button"
              class="ghost-btn"
              :disabled="pushBusy"
              @click="handleTestPush"
            >发条测试</button>
            <button
              v-if="profile.pushChannel"
              type="button"
              class="ghost-btn"
              :disabled="pushBusy"
              @click="handleDisablePush"
            >关闭推送</button>
          </div>
          <p v-if="profile.pushChannel && !pushForm.pushChannel" class="push-saved">
            当前已启用：{{ profile.pushChannel === 'bark' ? 'Bark' : 'Server酱' }}（Key 已保存，不在页面回显）
          </p>
          <p v-if="pushMsg" class="push-msg" role="status">{{ pushMsg }}</p>
        </div>
      </section>

      <!-- LLM 用量：成本口径整站可见（这是运营信息，不做按用户拆分） -->
      <section v-if="llmUsage && (llmUsage.totals?.usageEvents || 0) > 0" class="card">
        <h2>AI 用量（近 {{ llmUsage.days }} 天）</h2>
        <div class="llm-grid num">
          <div><span>LLM 调用</span><strong>{{ llmUsage.totals.llmCalls }}</strong></div>
          <div><span>消耗 tokens</span><strong>{{ Number(llmUsage.totals.llmTokens).toLocaleString() }}</strong></div>
          <div><span>工具调用</span><strong>{{ llmUsage.totals.toolCalls }}</strong></div>
        </div>
        <div v-if="llmDailySeries().length" class="llm-bars" aria-hidden="true">
          <div
            v-for="(d, i) in llmDailySeries()"
            :key="i"
            class="llm-bar"
            :style="{ height: Math.max(4, d.heightPct) + '%' }"
            :title="`${d.date}：${Number(d.llmTokens).toLocaleString()} tokens`"
          ></div>
        </div>
        <p class="llm-note">委员会每次开会约 3 万 tokens；预算硬顶由服务端配置（当前 {{ llmUsage.dailyTokenBudget > 0 ? llmUsage.dailyTokenBudget + ' tokens/天' : '未设置' }}）。</p>
      </section>

      <!-- 同花顺绑定 -->
      <section class="card">
        <h2>同花顺账号</h2>

        <!-- 已绑定 -->
        <template v-if="thsBound === true && !showBindQr">
          <p class="ths-state ok">已绑定</p>
          <p class="ths-detail">
            <span v-if="thsLastSync" class="num">上次同步：{{ String(thsLastSync).replace('T', ' ').slice(0, 19) }}</span>
            <span v-else>尚未同步过自选股</span>
            <span v-if="thsLastError" class="ths-err">· {{ thsLastError }}</span>
          </p>
          <div class="ths-btns">
            <button type="button" class="btn btn-outline" :disabled="working" @click="showBindQr = true">重新绑定</button>
            <button type="button" class="btn btn-text danger" :disabled="working" @click="handleUnbind">解绑</button>
          </div>
        </template>

        <!-- 未绑定 或 点开了重新绑定 -->
        <template v-else-if="thsBound === false || showBindQr">
          <p class="ths-detail">
            用手机同花顺 App 扫码绑定，绑定后自动同步自选股，以后也能直接扫码登录。
          </p>
          <QrLogin v-if="showBindQr" mode="bind" @success="onBindSuccess" />
          <div class="ths-btns">
            <button v-if="!showBindQr" type="button" class="btn btn-outline" @click="showBindQr = true">去绑定</button>
            <button v-else type="button" class="btn btn-text" @click="showBindQr = false">收起</button>
          </div>
        </template>

        <p v-else class="ths-detail">加载中...</p>
        <!-- 绑定/同步结果是动态插入的，用 role="status" 让屏幕阅读器也能听到 -->
        <p v-if="thsMsg" class="ths-msg num" role="status">{{ thsMsg }}</p>
      </section>

      <!-- 修改密码 -->
      <section class="card">
        <h2>安全设置</h2>
        <div v-if="!showPasswordForm">
          <button type="button" class="btn btn-outline" @click="showPasswordForm = true">修改密码</button>
        </div>
        <form v-else @submit.prevent="handleChangePassword" class="password-form">
          <!-- placeholder 不能当标签，用 .visually-hidden 的 label 提供可访问名称 -->
          <label class="visually-hidden" for="pwd-old">当前密码</label>
          <input id="pwd-old" v-model="passwordForm.oldPassword" type="password" placeholder="当前密码" required />
          <label class="visually-hidden" for="pwd-new">新密码（至少 6 位）</label>
          <input id="pwd-new" v-model="passwordForm.newPassword" type="password" placeholder="新密码（至少6位）" required />
          <label class="visually-hidden" for="pwd-confirm">确认新密码</label>
          <input id="pwd-confirm" v-model="passwordForm.confirmPassword" type="password" placeholder="确认新密码" required />
          <p v-if="pwdError" class="error" role="alert">{{ pwdError }}</p>
          <p v-if="pwdSuccess" class="success" role="status">{{ pwdSuccess }}</p>
          <div class="edit-actions">
            <button type="button" class="btn btn-text" @click="showPasswordForm = false">取消</button>
            <button type="submit" class="btn btn-primary" :disabled="loading">
              {{ loading ? '修改中...' : '确认修改' }}
            </button>
          </div>
        </form>
      </section>

      <!-- 退出登录 -->
      <section class="card">
        <button type="button" class="btn btn-danger btn-full" @click="handleLogout">退出登录</button>
      </section>
  </div>
</template>

<style scoped>
/* 100vh 在移动浏览器里会把地址栏高度算进去，补 100dvh 兜底 */



.card { background: var(--color-bg-surface); border-radius: var(--radius-lg); padding: 24px; margin-bottom: 16px; box-shadow: var(--shadow-1); }
.card h2 { font-size: 16px; margin-bottom: 16px; color: var(--color-text-primary); }

/* ---------- 消息推送 ---------- */
.push-hint { margin: -8px 0 14px; font-size: 12.5px; color: var(--color-text-muted); }
.push-form { display: flex; flex-direction: column; gap: 12px; }
.push-field { display: flex; flex-direction: column; gap: 4px; }
.push-field.wide input { width: 100%; }
.push-field span { font-size: 12px; color: var(--color-text-secondary); }
.push-field select, .push-field input {
  padding: 8px 10px;
  border: 1px solid var(--color-border-strong);
  border-radius: var(--radius-md);
  font-size: 14px;
  background: var(--color-bg-surface);
}
.push-actions { display: flex; gap: 8px; flex-wrap: wrap; }
.push-saved, .push-msg { margin: 0; font-size: 12.5px; color: var(--color-text-secondary); }
.push-msg { color: var(--color-accent); }

/* ---------- AI 用量 ---------- */
.llm-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 12px; }
.llm-grid > div { display: flex; flex-direction: column; gap: 4px; }
.llm-grid span { font-size: 12px; color: var(--color-text-secondary); }
.llm-grid strong { font-size: 16px; font-weight: 600; }
.llm-bars { display: flex; align-items: flex-end; gap: 3px; height: 60px; }
.llm-bar { flex: 1; min-width: 6px; border-radius: 2px 2px 0 0; background: var(--color-accent-soft); border-top: 2px solid var(--color-accent); }
.llm-note { margin: 8px 0 0; font-size: 12px; color: var(--color-text-muted); }

.info-grid { display: flex; flex-direction: column; gap: 12px; }
.info-item { display: flex; justify-content: space-between; align-items: center; padding: 8px 0; border-bottom: 1px solid var(--color-border); }
.info-item .label { color: var(--color-text-secondary); font-size: 14px; }
.info-item .value { color: var(--color-text-primary); font-size: 14px; font-weight: 500; }

.password-form { display: flex; flex-direction: column; gap: 12px; }
.password-form input { width: 100%; padding: 10px 12px; border: 1px solid var(--color-border-control); border-radius: var(--radius-sm); font-size: 14px; box-sizing: border-box; }
/* 焦点：删掉 outline: none（旧值只剩 1px 边框 + #4096ff，对白仅 2.99:1），
   交给 style.css 的全局 :focus-visible 环；边框变色只作第二通道。 */
.password-form input:focus-visible { border-color: var(--color-accent); }

.edit-actions { display: flex; gap: 8px; justify-content: flex-end; margin-top: 8px; }

.btn {
  padding: 8px 20px; border: none; border-radius: var(--radius-sm); font-size: 14px; cursor: pointer;
  transition: background-color var(--duration-base) var(--ease-out),
              border-color var(--duration-base) var(--ease-out),
              color var(--duration-base) var(--ease-out);
}
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
.btn-primary { background: var(--color-accent); color: var(--color-text-on-accent); }
.btn-primary:hover:not(:disabled) { background: var(--color-accent-hover); }
/* 退出登录是"危险但非破坏性"的动作（只清本地 token，不销毁任何数据），
   所以不占用整块实心红：页面唯一的实心主操作留给"确认修改"。
   改用中性描边 + 危险色文字/边框（#cf1322 对白 5.57:1，边框同样 ≥3:1），
   悬停时才填充实心危险色（白字对 #cf1322 为 5.57:1）。 */
.btn-danger { background: transparent; border: 1px solid var(--color-danger); color: var(--color-danger); }
.btn-danger:hover:not(:disabled) { background: var(--color-danger); color: var(--color-text-on-accent); }
.btn-outline { background: transparent; border: 1px solid var(--color-border-control); color: var(--color-text-primary); }
.btn-outline:hover { border-color: var(--color-accent); color: var(--color-accent); }
.btn-text { background: transparent; color: var(--color-text-muted); }
.btn-text:hover { color: var(--color-text-primary); }
.btn-full { width: 100%; padding: 12px; font-size: 15px; }

.error { color: var(--color-danger); font-size: 13px; }
.success { color: var(--color-success); font-size: 13px; }

/* ---- 同花顺绑定卡片 ---- */
.ths-state { font-size: 14px; font-weight: 600; margin: 0 0 8px; }
.ths-state.ok { color: var(--color-success); }
.ths-state.ok::before { content: '● '; font-size: 12px; color: var(--color-success-mark); }
.ths-detail { font-size: 13px; color: var(--color-text-secondary); line-height: 1.6; margin: 0 0 14px; }
.ths-err { color: var(--color-danger); }
.ths-btns { display: flex; gap: 8px; flex-wrap: wrap; }
/* 解绑：文字型危险按钮。悬停只加下划线 —— 没有比 --color-danger 更深的红色令牌，
   旧值 #ff7875 对白仅 2.56:1，不能承载文字，所以不用"变浅"来表悬停。 */
.btn-text.danger { color: var(--color-danger); }
.btn-text.danger:hover { color: var(--color-danger); text-decoration: underline; }
.ths-msg { font-size: 13px; color: var(--color-accent); margin: 12px 0 0; }
</style>
