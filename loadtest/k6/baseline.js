// 场景一（基线）：5 个并发跑 3 分钟只读混合请求，给所有接口建立延迟/错误率参考值。
// 这一步同时完成 stub 缓存的录制（Java 首次请求各行情接口，代理顺带落盘），
// 所以它必须第一个跑——后面的高压场景才不会漏流量到真实上游。
//
// 用法：k6 run baseline.js
// 可选：-e BASE_URL=http://localhost:8080  -e SYMBOLS=600519,000001,300750
import { sleep } from 'k6';
import { registerOrLogin, hitHotPaths } from './lib.js';

const BASE = __ENV.BASE_URL || 'http://localhost:8080';
const OFFSET = Number(__ENV.USER_OFFSET || 0);
const SYMBOLS = (__ENV.SYMBOLS || '600519,000001,300750').split(',');
const KEYWORD = __ENV.KEYWORD || '茅台';

export const options = {
  vus: 5,
  duration: '3m',
  thresholds: {
    // 基线阶段任何失败都不正常：不是拐点问题，是环境/接口问题，先修再继续
    http_req_failed: ['rate<0.01'],
  },
};

export function setup() {
  const tokens = [];
  for (let i = 0; i < 5; i++) tokens.push(registerOrLogin(BASE, OFFSET + i));
  return { tokens };
}

export default function (data) {
  const token = data.tokens[(__VU - 1) % data.tokens.length];
  const symbol = SYMBOLS[(__VU + __ITER) % SYMBOLS.length];
  hitHotPaths(BASE, token, symbol, KEYWORD);
  sleep(1); // 盯盘用户约每秒刷新一轮
}
