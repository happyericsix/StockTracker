<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { Smartphone, ArrowLeft, CandlestickChart } from 'lucide-vue-next'
import { login, register } from '../api/auth.js'
import QrLogin from '../components/QrLogin.vue'
import Button from '../components/ui/button.vue'
import BeianFooter from '../components/BeianFooter.vue'

const router = useRouter()
const isLogin = ref(true)
const form = ref({ username: '', password: '', email: '', phone: '' })
const error = ref('')
const submitting = ref(false)

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
    submitting.value = true
    error.value = ''
    const payload = isLogin.value
      ? { username: form.value.username, password: form.value.password }
      : {
          username: form.value.username,
          email: form.value.email,
          phone: form.value.phone,
          password: form.value.password,
        }
    const res = isLogin.value ? await login(payload) : await register(payload)
    localStorage.setItem('token', res.data.token)
    router.push('/dashboard')
  } catch (e) {
    // 兜底文案必须说明怎么恢复，不能只说"操作失败"
    error.value = e.response?.data?.message
      || (isLogin.value
        ? '登录失败，请检查用户名和密码后重试'
        : '注册失败，请检查填写的信息后重试')
  } finally {
    submitting.value = false
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
  <!-- 墨色底 + 卡面：登录即"走进墨阶"——侧栏的墨色在这里先出场 -->
  <div class="relative flex min-h-screen min-h-[100dvh] items-center justify-center bg-sidebar px-4 py-10">
    <div class="w-full max-w-[400px]">
      <div class="rounded-xl border border-border bg-surface p-7">
        <!-- 页面唯一 h1：品牌名（标题大纲不跳级） -->
        <div class="mb-6 flex flex-col items-center gap-2.5">
          <span class="flex h-12 w-12 items-center justify-center rounded-xl bg-accent-soft text-accent">
            <CandlestickChart :size="26" :stroke-width="1.75" aria-hidden="true" />
          </span>
          <h1 class="text-[20px] font-semibold text-ink">Stock Tracker</h1>
          <p class="text-[12.5px] text-ink-2">个人 A 股模拟盘助手</p>
        </div>

        <!-- ========== 账号密码（主入口） ========== -->
        <template v-if="!showQr">
          <h2 class="sr-only">{{ isLogin ? '登录' : '注册' }}</h2>
          <form class="space-y-3.5" @submit.prevent="submit">
            <!-- label 隐藏但存在（placeholder 不能当标签） -->
            <label class="sr-only" for="login-username">用户名、手机号或邮箱</label>
            <input
              id="login-username"
              v-model="form.username"
              placeholder="用户名 / 手机号 / 邮箱"
              autocomplete="username"
              required
              class="h-[38px] w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-ink transition-colors placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
            />
            <template v-if="!isLogin">
              <label class="sr-only" for="login-email">邮箱（选填）</label>
              <input
                id="login-email"
                v-model="form.email"
                placeholder="邮箱（选填）"
                autocomplete="email"
                class="h-[38px] w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-ink transition-colors placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
              />
              <label class="sr-only" for="login-phone">手机号（选填）</label>
              <input
                id="login-phone"
                v-model="form.phone"
                placeholder="手机号（选填）"
                autocomplete="tel"
                class="h-[38px] w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-ink transition-colors placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
              />
            </template>
            <label class="sr-only" for="login-password">密码</label>
            <input
              id="login-password"
              v-model="form.password"
              type="password"
              placeholder="密码"
              :autocomplete="isLogin ? 'current-password' : 'new-password'"
              required
              class="h-[38px] w-full rounded-lg border border-border bg-surface px-3 text-[13px] text-ink transition-colors placeholder:text-ink-3 focus:border-accent focus:outline-none focus:ring-2 focus:ring-[color:var(--ring)]"
            />
            <div v-if="isLogin" class="flex justify-end">
              <button
                type="button"
                class="rounded px-0.5 py-1 text-[12.5px] text-accent transition-colors hover:text-accent-hover"
                @click="error = '请联系管理员重置密码'"
              >忘记密码？</button>
            </div>
            <p v-if="error" class="text-[12.5px] leading-relaxed text-up" role="alert">{{ error }}</p>
            <Button type="submit" variant="primary" block :loading="submitting">
              {{ isLogin ? '登录' : '注册' }}
            </Button>
          </form>
          <button
            type="button"
            class="mt-3.5 w-full py-1 text-center text-[13px] text-accent transition-colors hover:text-accent-hover"
            @click="toggleMode"
          >
            {{ isLogin ? '没有账号？立即注册' : '已有账号？立即登录' }}
          </button>

          <!-- ========== 同花顺扫码入口 ========== -->
          <div class="mt-5">
            <div class="flex items-center gap-3 text-[12px] text-ink-3">
              <span class="h-px flex-1 bg-border"></span>或<span class="h-px flex-1 bg-border"></span>
            </div>
            <button
              type="button"
              class="mt-3.5 flex h-[38px] w-full items-center justify-center gap-2 rounded-lg border border-accent bg-surface text-[13px] font-medium text-accent transition-colors hover:bg-accent-soft"
              @click="toggleQr"
            >
              <Smartphone :size="16" :stroke-width="1.75" aria-hidden="true" /> 同花顺扫码登录
            </button>
            <p class="mt-2 text-left text-[12px] leading-relaxed text-ink-3">
              免注册、免记密码，用手机同花顺 App 扫一下即可登录。
            </p>
          </div>
        </template>

        <!-- ========== 扫码面板 ========== -->
        <template v-else>
          <h2 class="mb-4 text-center text-[14px] font-medium text-ink-2">同花顺扫码登录</h2>
          <QrLogin mode="login" @success="onQrSuccess" />
          <p class="mt-3 text-center text-[12px] leading-relaxed text-ink-3">
            首次扫码会自动为你创建一个账号，以后直接扫码就能进。
          </p>
          <button
            type="button"
            class="mt-4 flex w-full items-center justify-center gap-1 py-1 text-[13px] text-accent transition-colors hover:text-accent-hover"
            @click="toggleQr"
          >
            <ArrowLeft :size="14" :stroke-width="2" aria-hidden="true" /> 返回账号密码登录
          </button>
        </template>
      </div>

      <p class="mt-5 text-center text-[11.5px] text-sidebar-dim">
        仅供学习交流，不构成投资建议
      </p>
    </div>
    <!-- 备案合规：匿名访客的第一屏也必须能看到备案号（footer 组件自带浅色适配） -->
    <BeianFooter class="absolute inset-x-0 bottom-0 text-sidebar-dim [&_a]:text-sidebar-dim [&_a:hover]:text-white/70" />
  </div>
</template>
