<script setup>
import { ref } from "vue"
import { useRouter } from "vue-router"
import { login, register } from "../api/auth.js"
import QrLogin from "../components/QrLogin.vue"
import AppIcon from "../components/AppIcon.vue"

const router = useRouter()
const isLogin = ref(true)
const form = ref({ username: '', password: '', email: '', phone: '' })
const error = ref('')

// 同花顺扫码入口默认收起 —— 账号密码仍是主入口，
// 扫码作为"更快捷的选择"放在下面。
const showQr = ref(false)

const toggleMode = () => {
  isLogin.value = !isLogin.value
  error.value = ''
  showQr.value = false
}

const submit = async () => {
  try {
    error.value = ''
    const payload = isLogin.value
      ? { username: form.value.username, password: form.value.password }
      : {
          username: form.value.username,
          email: form.value.email,
          phone: form.value.phone,
          password: form.value.password
        }
    const res = isLogin.value
      ? await login(payload)
      : await register(payload)
    localStorage.setItem('token', res.data.token)
    router.push('/dashboard')
  } catch (e) {
    // 兜底文案必须说明怎么恢复：旧值"操作失败"只说了失败，没说下一步做什么
    error.value = e.response?.data?.message
      || (isLogin.value
        ? '登录失败，请检查用户名和密码后重试'
        : '注册失败，请检查填写的信息后重试')
  }
}

const toggleQr = () => {
  showQr.value = !showQr.value
  error.value = ''
}

/** 扫码确认成功：后端已签发 JWT，直接进首页 */
const onQrSuccess = ({ token }) => {
  if (token) {
    localStorage.setItem('token', token)
  }
  // 稍等一下让用户看到"登录成功"的提示，再跳转
  setTimeout(() => router.push('/dashboard'), 600)
}
</script>

<template>
  <div class="login-container">
    <div class="login-card">
      <!-- 页面唯一的一级标题：全部页面都以品牌名作 h1（与 Dashboard 的 header h1 一致），
           登录/注册作为其下二级标题，标题大纲才不跳级 -->
      <h1 class="brand">Stock Tracker</h1>

      <!-- ========== 账号密码登录（主入口，保持原样） ========== -->
      <template v-if="!showQr">
        <h2>{{ isLogin ? '登录' : '注册' }}</h2>
        <form @submit.prevent="submit">
          <!-- placeholder 不能当标签（输入后即消失），用 .visually-hidden 的 label 提供可访问名称 -->
          <label class="visually-hidden" for="login-username">用户名、手机号或邮箱</label>
          <input id="login-username" v-model="form.username" placeholder="用户名 / 手机号 / 邮箱" required />
          <template v-if="!isLogin">
            <label class="visually-hidden" for="login-email">邮箱（选填）</label>
            <input id="login-email" v-model="form.email" placeholder="邮箱（选填）" />
            <label class="visually-hidden" for="login-phone">手机号（选填）</label>
            <input id="login-phone" v-model="form.phone" placeholder="手机号（选填）" />
          </template>
          <label class="visually-hidden" for="login-password">密码</label>
          <input id="login-password" v-model="form.password" type="password" placeholder="密码" required />
          <div v-if="isLogin" class="forgot-row">
            <!-- 原来是 <span @click>：指针可达、键盘完全到不了，换成真按钮 -->
            <button type="button" class="forgot" @click="error = '请联系管理员重置密码'">忘记密码？</button>
          </div>
          <!-- 报错是动态插入的，加 role="alert" 屏幕阅读器才会念出来 -->
          <p v-if="error" class="error" role="alert">{{ error }}</p>
          <button type="submit">{{ isLogin ? '登录' : '注册' }}</button>
        </form>
        <!-- 原来是 <p @click>：同样键盘不可达，换成真按钮 -->
        <button type="button" class="toggle" @click="toggleMode">
          {{ isLogin ? '没有账号？立即注册' : '已有账号？立即登录' }}
        </button>

        <!-- ========== 同花顺扫码登录入口 ========== -->
        <div class="qr-entry">
          <div class="divider"><span>或</span></div>
          <button type="button" class="qr-trigger" @click="toggleQr">
            <AppIcon name="smartphone" :size="17" :stroke-width="2" /> 同花顺扫码登录
          </button>
          <p class="qr-note">
            更快一步 —— 免注册、免记密码，用手机同花顺 App 扫一下即可登录。
          </p>
        </div>
      </template>

      <!-- ========== 扫码面板 ========== -->
      <template v-else>
        <h2>同花顺扫码登录</h2>
        <QrLogin mode="login" @success="onQrSuccess" />
        <p class="qr-note qr-note-center">
          首次扫码会自动为你创建一个账号，以后直接扫码就能进。
        </p>
        <button type="button" class="toggle" @click="toggleQr">
          <span aria-hidden="true">←</span> 返回账号密码登录
        </button>
      </template>
    </div>
  </div>
</template>

<style scoped>
/* 100vh 在移动浏览器里会把地址栏高度算进去，补 100dvh 兜底。
   容器补内距：旧实现没有内距，320px 下卡片直接贴到视口边缘。 */
.login-container { display: flex; justify-content: center; align-items: center; min-height: 100vh; min-height: 100dvh; padding: var(--space-4); background: var(--color-bg-page); }
/* width: min(...) 代替固定 380px —— 固定值靠 flex-shrink 兜底才能不溢出，
   是"碰巧没坏"而不是"构造上不会坏"。 */
.login-card { background: var(--color-bg-surface); padding: var(--space-7); border-radius: var(--radius-3xl); box-shadow: var(--shadow-1); width: min(400px, 100%); }
.brand { text-align: center; font: var(--font-title); color: var(--color-text-primary); margin-bottom: var(--space-2); }
h2 { text-align: center; margin-bottom: var(--space-5); color: var(--color-text-secondary); font: var(--font-body); }
/* 输入框用 16px（--font-body）：小于 16px 时 iOS Safari 会把整页放大。
   旧值 14px 在 iPhone 上点一下输入框页面就跳一下。 */
input { width: 100%; padding: var(--space-3); margin-bottom: var(--space-4); border: 1px solid var(--color-border-control); border-radius: var(--radius-sm); font: var(--font-body); box-sizing: border-box; }
/* 焦点：删掉 outline: none（旧值只靠 1px 边框 + #4096ff 撑，对白仅 2.99:1），
   让 style.css 的全局 :focus-visible 环接管；边框变色只作第二通道。 */
input:focus-visible { border-color: var(--color-accent); }
.forgot-row { text-align: right; margin-bottom: var(--space-4); margin-top: calc(-1 * var(--space-2)); }
/* 按钮默认样式显式重置；上下留 4px 内边距让命中区达到 24×24（WCAG 2.5.8） */
.forgot {
  display: inline-block; width: auto; padding: var(--space-1) 0;
  background: none; border: none; text-align: inherit;
  color: var(--color-accent); font: var(--font-caption); cursor: pointer;
}
/* 注意：下面的裸 button:hover 规则会命中所有按钮，
   不显式重置背景的话，悬停会被染成实心主色，而文字色同为深蓝 → 按钮文字看不见。 */
.forgot:hover { background: none; color: var(--color-accent-hover); }
button { width: 100%; padding: var(--space-3); background: var(--color-accent); color: var(--color-text-on-accent); border: none; border-radius: var(--radius-sm); font: var(--font-body); font-weight: 500; cursor: pointer; }
button:hover { background: var(--color-accent-hover); }
.error { color: var(--color-danger); font: var(--font-caption); margin-bottom: var(--space-3); }
/* 换成按钮后保持原来"居中一行文字"的外观，同时整行都可点 */
.toggle {
  display: block; width: 100%; padding: var(--space-1) 0; margin-top: var(--space-4);
  background: none; border: none; text-align: center;
  color: var(--color-accent); font: var(--font-ui); cursor: pointer;
}
/* 同上：显式重置悬停背景，避免被裸 button:hover 规则染成实心主色 */
.toggle:hover { background: none; color: var(--color-accent-hover); }

/* ---- 同花顺扫码入口 ---- */
.qr-entry { margin-top: var(--space-5); }
.divider { display: flex; align-items: center; gap: var(--space-3); margin: var(--space-4) 0; color: var(--color-text-muted); font: var(--font-caption); }
.divider::before, .divider::after { content: ''; flex: 1; height: 1px; background: var(--color-border); }
/* 文字型按钮：文字与边框都用达标的 --color-accent（旧 #1677ff 对白仅 4.10:1） */
.qr-trigger {
  background: var(--color-bg-surface); color: var(--color-accent); border: 1px solid var(--color-accent);
  font: var(--font-ui); display: flex; align-items: center; justify-content: center; gap: var(--space-2);
}
.qr-trigger:hover { background: var(--color-accent-soft); }
.qr-note { font: var(--font-caption); color: var(--color-text-muted); text-align: left; margin: var(--space-2) 0 0; line-height: 1.5; }
.qr-note-center { text-align: center; }
</style>
