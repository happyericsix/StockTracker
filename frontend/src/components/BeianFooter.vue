<script setup>
/**
 * 合规备案 footer（全站共用）：
 * - ICP 备案号必须挂页面底部并链接工信部 beian.miit.gov.cn（备案通过的合规要求）。
 *   2026-10-24 从 AppShell 抽出：Login / Share 在鉴权壳之外，但工信部的要求是
 *   全站可见——匿名访客的第一屏（/login）不挂等于没挂。
 * - 公安备案号审核通过后填进来即可显示（空串 = 整块不渲染），链接公安平台。
 */
const ICP_BEIAN = '浙ICP备2026090205号-1'
const ICP_BEIAN_URL = 'https://beian.miit.gov.cn/'
const GONGAN_BEIAN = ''   // 公安备案通过后填，如 '浙公网安备 330106020xxxxx号'
const GONGAN_BEIAN_URL = 'https://beian.mps.gov.cn/'
// 构建时刻（vite.config.js define 注入）：排查"我跑的哪版 / 服务器哪版"用，
// 与 main.js 的更新提示条配套——更新生效与否从此不用猜
const APP_VERSION = __APP_VERSION__
</script>

<template>
  <footer class="mx-auto w-full max-w-[1200px] px-4 pb-6 sm:px-6 lg:px-8">
    <p class="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-[12px] text-ink-3">
      <!-- py-1.5：备案链接也是链接，19px 的纯文字在手机上基本点不中 -->
      <a :href="ICP_BEIAN_URL" target="_blank" rel="noopener noreferrer"
         class="inline-flex items-center py-1.5 transition-colors hover:text-ink">{{ ICP_BEIAN }}</a>
      <a v-if="GONGAN_BEIAN" :href="GONGAN_BEIAN_URL" target="_blank" rel="noopener noreferrer"
         class="inline-flex items-center py-1.5 transition-colors hover:text-ink">{{ GONGAN_BEIAN }}</a>
      <span class="num inline-flex items-center py-1.5">v{{ APP_VERSION }}</span>
    </p>
  </footer>
</template>
