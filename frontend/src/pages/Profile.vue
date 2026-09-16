<script setup>
import { ref, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getProfile, changePassword } from '../api/user.js'
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
    }
  } catch (e) {
    // 如果后端暂无接口，使用 localStorage 兜底
    const stored = localStorage.getItem('user_profile')
    if (stored) {
      try { profile.value = JSON.parse(stored) } catch {}
    }
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
  localStorage.removeItem('user_profile')
  router.push('/login')
}

const goBack = () => router.push('/dashboard')

onMounted(() => {
  fetchProfile()
  loadThsStatus()
})
</script>

<template>
  <div class="profile-page">
    <header>
      <button type="button" class="back-btn" @click="goBack"><span aria-hidden="true">←</span> 返回</button>
      <h1>个人中心</h1>
    </header>
    <main>
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
    </main>
  </div>
</template>

<style scoped>
/* 100vh 在移动浏览器里会把地址栏高度算进去，补 100dvh 兜底 */
.profile-page { min-height: 100vh; min-height: 100dvh; background: var(--color-bg-page); }
/* 最外层 header：index.html 声明了 black-translucent，iOS 独立模式下内容会顶到状态栏下 */
header {
  background: var(--color-bg-inverse); color: var(--color-text-inverse);
  padding: 16px 24px; padding-top: calc(16px + env(safe-area-inset-top, 0px));
  display: flex; align-items: center; gap: 16px;
}
header h1 { margin: 0; font-size: 20px; }
.back-btn { background: transparent; border: 1px solid var(--color-text-inverse); color: var(--color-text-inverse); padding: 6px 16px; border-radius: var(--radius-sm); cursor: pointer; }
/* 旧值 rgba(255,255,255,0.1) 没有对应令牌；这里用 color-mix 从 --color-text-inverse
   派生同样的 10% 白色叠层，不新增字面色值，渲染结果与原值一致 */
.back-btn:hover { background: color-mix(in srgb, var(--color-text-inverse) 10%, transparent); }
main { max-width: 600px; margin: 0 auto; padding: 24px 16px; }

.card { background: var(--color-bg-surface); border-radius: var(--radius-lg); padding: 24px; margin-bottom: 16px; box-shadow: var(--shadow-1); }
.card h2 { font-size: 16px; margin-bottom: 16px; color: var(--color-text-primary); }

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
