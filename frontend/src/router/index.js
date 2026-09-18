import { createRouter, createWebHistory } from 'vue-router'
import Login from '../pages/Login.vue'
import Dashboard from '../pages/Dashboard.vue'
import KLineChart from '../pages/KLineChart.vue'
import Alerts from '../pages/Alerts.vue'
import Assistant from '../pages/Assistant.vue'
import Messages from '../pages/Messages.vue'
import Profile from '../pages/Profile.vue'
import Strategies from '../pages/Strategies.vue'
import StrategyDetail from '../pages/StrategyDetail.vue'
import Paper from '../pages/Paper.vue'
import Memory from '../pages/Memory.vue'

const routes = [
  { path: '/', redirect: '/dashboard' },
  { path: '/login', component: Login },
  { path: '/dashboard', component: Dashboard, meta: { requiresAuth: true } },
  { path: '/chart/:symbol', component: KLineChart, meta: { requiresAuth: true } },
  { path: '/alerts', component: Alerts, meta: { requiresAuth: true } },
  { path: '/assistant', component: Assistant, meta: { requiresAuth: true } },
  { path: '/messages', component: Messages, meta: { requiresAuth: true } },
  { path: '/profile', component: Profile, meta: { requiresAuth: true } },
  { path: '/strategies', component: Strategies, meta: { requiresAuth: true } },
  { path: '/strategies/:id', component: StrategyDetail, meta: { requiresAuth: true } },
  // 模拟盘是**顶级入口**：它的读视图是跨策略的（在跑什么/赚没赚/今天动没动/下次何时），
  // 藏进"某条策略的详情页最下面"正是"用户不知道有这个功能"的原因。
  { path: '/paper', component: Paper, meta: { requiresAuth: true } },
  { path: '/memory', component: Memory, meta: { requiresAuth: true } },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  if (to.meta.requiresAuth && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/dashboard')
  } else {
    next()
  }
})

export default router
