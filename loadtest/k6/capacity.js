// 场景二（容量阶梯）：固定并发跑一段，逐级抬升找系统拐点。
// 一级一级单独跑（而不是一个脚本里自动爬梯），每级的 P95/P99/错误率
// 都是干净的一次运行结果，拐点判断不掺前一级的余热：
//
//   k6 run capacity.js -e VUS=50  -e DURATION=3m
//   k6 run capacity.js -e VUS=100 -e DURATION=3m
//   k6 run capacity.js -e VUS=200 -e DURATION=3m
//   k6 run capacity.js -e VUS=400 -e DURATION=3m
//
// 拐点定义：错误率 >1% 或 P99 超过基线 3 倍的那一级。前一级 = 当前系统容量。
// 阈值故意放宽（5%/2s）：找拐点的过程里允许临时越线，最终判定看每级记录。
import { sleep } from 'k6';
import { registerOrLogin, hitHotPaths } from './lib.js';

const BASE = __ENV.BASE_URL || 'http://localhost:8080';
const VUS = Number(__ENV.VUS || 50);
const DURATION = __ENV.DURATION || '3m';
const OFFSET = Number(__ENV.USER_OFFSET || 0);
const SYMBOLS = (__ENV.SYMBOLS || '600519,000001,300750').split(',');
const KEYWORD = __ENV.KEYWORD || '茅台';

export const options = {
  vus: VUS,
  duration: DURATION,
  thresholds: {
    http_req_failed: ['rate<0.05'],
    http_req_duration: ['p(99)<2000'],
  },
};

export function setup() {
  // JWT 无状态，多个 VU 共享账号不影响被测行为；50 个账号封顶，省注册
  const n = Math.min(VUS, 50);
  const tokens = [];
  for (let i = 0; i < n; i++) tokens.push(registerOrLogin(BASE, OFFSET + i));
  return { tokens };
}

export default function (data) {
  const token = data.tokens[(__VU - 1) % data.tokens.length];
  const symbol = SYMBOLS[(__VU + __ITER) % SYMBOLS.length];
  hitHotPaths(BASE, token, symbol, KEYWORD);
  sleep(0.5); // 高压下用户刷新更频繁
}
