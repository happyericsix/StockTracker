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
import NewsSearch from '../pages/NewsSearch.vue'
import AppShell from '../components/AppShell.vue'

const routes = [
  { path: '/login', component: Login },
  // 鉴权页统一挂在 AppShell 下：左侧导航 + 内容区一处实现、全站一致
  {
    path: '/',
    component: AppShell,
    meta: { requiresAuth: true },
    children: [
      { path: '', redirect: '/dashboard' },
      { path: 'dashboard', component: Dashboard },
      { path: 'chart/:symbol', component: KLineChart },
      { path: 'alerts', component: Alerts },
      { path: 'assistant', component: Assistant },
      { path: 'messages', component: Messages },
      { path: 'profile', component: Profile },
      { path: 'strategies', component: Strategies },
      { path: 'strategies/:id', component: StrategyDetail },
      // 模拟盘是**顶级入口**：它的读视图是跨策略的（在跑什么/赚没赚/今天动没动/下次何时），
      // 藏进"某条策略的详情页最下面"正是"用户不知道有这个功能"的原因。
      { path: 'paper', component: Paper },
      { path: 'memory', component: Memory },
      // N4 资讯搜索页：后端 /news/search 早就就绪，这条路由把它接到用户面前
      { path: 'news', component: NewsSearch },
      // 没有这条时访问未注册路径（手滑/旧链接）渲染一片空白页，没有任何反馈
      { path: ':pathMatch(.*)*', redirect: '/dashboard' },
    ],
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  const token = localStorage.getItem('token')
  const requiresAuth = to.matched.some((record) => record.meta.requiresAuth)
  if (requiresAuth && !token) {
    next('/login')
  } else if (to.path === '/login' && token) {
    next('/dashboard')
  } else {
    next()
  }
})

export default router
