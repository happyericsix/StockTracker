// 场景三（开盘冲击）：模拟 9:25 集合竞价——20 秒内从 10 并发冲到 300，
// 高位维持 90 秒后撤退，最后留 60 秒小流量观察"恢复能力"（P99 是否回落）。
//
// 用法：k6 run spike.js    （可 -e PEAK=500 抬高峰值）
// 看什么：峰值期错误率、最后 60 秒延迟是否回到基线水平（回不去=有线程/连接堆积）。
import { sleep } from 'k6';
import { registerOrLogin, hitHotPaths } from './lib.js';

const BASE = __ENV.BASE_URL || 'http://localhost:8080';
const PEAK = Number(__ENV.PEAK || 300);
const OFFSET = Number(__ENV.USER_OFFSET || 0);
const SYMBOLS = (__ENV.SYMBOLS || '600519,000001,300750').split(',');
const KEYWORD = __ENV.KEYWORD || '茅台';

export const options = {
  scenarios: {
    open_surge: {
      executor: 'ramping-vus',
      startVUs: 0,
      stages: [
        { duration: '30s', target: 10 }, // 平静期
        { duration: '20s', target: PEAK }, // 开盘冲击
        { duration: '90s', target: PEAK }, // 高位维持
        { duration: '10s', target: 0 }, // 撤退
        { duration: '60s', target: 10 }, // 恢复观察
      ],
      gracefulRampDown: '30s',
    },
  },
  thresholds: {
    // 冲击场景放宽到 2% / 3s：目标是"扛住并恢复"，不是零错误
    http_req_failed: ['rate<0.02'],
    http_req_duration: ['p(99)<3000'],
  },
};

export function setup() {
  const tokens = [];
  for (let i = 0; i < 50; i++) tokens.push(registerOrLogin(BASE, OFFSET + i));
  return { tokens };
}

export default function (data) {
  const token = data.tokens[(__VU - 1) % data.tokens.length];
  const symbol = SYMBOLS[(__VU + __ITER) % SYMBOLS.length];
  hitHotPaths(BASE, token, symbol, KEYWORD);
  sleep(0.3); // 冲击期用户疯狂刷新
}
