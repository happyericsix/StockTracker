// 场景四（浸泡）：恒定并发长时间运行，专测资源泄漏——内存只爬不落、
// 连接池被慢慢占满、SSE/Redis 连接泄漏这类"短跑测不出"的问题。
//
// 用法：k6 run soak.js                       # 默认 50 并发 30 分钟（本地快速版）
//       k6 run soak.js -e VUS=100 -e DURATION=2h   # 完整版
//
// 判定：期间另开终端跑 check-metrics.ps1，看 jvm.memory.used 的走势——
// 正常是锯齿（GC 回落），持续爬升不回落 = 泄漏。
import { sleep } from 'k6';
import { registerOrLogin, hitHotPaths } from './lib.js';

const BASE = __ENV.BASE_URL || 'http://localhost:8080';
const VUS = Number(__ENV.VUS || 50);
const DURATION = __ENV.DURATION || '30m';
const OFFSET = Number(__ENV.USER_OFFSET || 0);
const SYMBOLS = (__ENV.SYMBOLS || '600519,000001,300750').split(',');
const KEYWORD = __ENV.KEYWORD || '茅台';

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(99)<1500'],
  },
};

export function setup() {
  const n = Math.min(VUS, 50);
  const tokens = [];
  for (let i = 0; i < n; i++) tokens.push(registerOrLogin(BASE, OFFSET + i));
  return { tokens };
}

export default function (data) {
  const token = data.tokens[(__VU - 1) % data.tokens.length];
  const symbol = SYMBOLS[(__VU + __ITER) % SYMBOLS.length];
  hitHotPaths(BASE, token, symbol, KEYWORD);
  sleep(1);
}
