// 公共库：所有 k6 场景脚本 import 这里的测试账号逻辑与热点请求。
// 测试账号只在本地 MySQL 里注册（loadtest_0..N），密码固定；
// setup() 每次脚本启动只跑一遍，不会把压测流量混进 auth 接口。
import http from 'k6/http';
import { check } from 'k6';

export const PASSWORD = 'LoadTest#2026';
export const USERNAME_PREFIX = 'loadtest';

function tryParseJson(res) {
  try {
    return res.json();
  } catch (_) {
    return null;
  }
}

// 先登录（上次跑过直接复用），失败再注册。两个都失败才抛错。
export function registerOrLogin(base, i) {
  const username = `${USERNAME_PREFIX}_${i}`;
  const jsonHeaders = { headers: { 'Content-Type': 'application/json' } };

  const login = http.post(
    `${base}/api/v1/auth/login`,
    JSON.stringify({ username, password: PASSWORD }),
    jsonHeaders
  );
  let parsed = login.status === 200 ? tryParseJson(login) : null;
  if (parsed && parsed.token) return parsed.token;

  const register = http.post(
    `${base}/api/v1/auth/register`,
    JSON.stringify({
      username,
      email: `${username}@loadtest.local`,
      phone: `138${String(10000000 + i).slice(-8)}`,
      password: PASSWORD,
    }),
    jsonHeaders
  );
  parsed = tryParseJson(register);
  if (register.status === 200 && parsed && parsed.token) return parsed.token;

  throw new Error(
    `测试账号 ${username} 登录/注册均失败（register 状态码 ${register.status}）。` +
      `请确认：应用已连本地 MySQL、注册开关开启（本地默认开）、` +
      `或换个 USER_OFFSET 起始编号重试`
  );
}

export function authHeaders(token) {
  return { Authorization: `Bearer ${token}` };
}

// 打包四个只读热点请求（自选股摘要 / 个股详情 / 搜索 / 市场状态），
// 模拟"开盘盯盘"用户的行为画像。每个请求带 name tag，结果按接口分列。
export function hitHotPaths(base, token, symbol, keyword) {
  const headers = authHeaders(token);

  const favs = http.get(`${base}/api/v1/user/favorites/brief`, {
    headers,
    tags: { name: 'favorites_brief' },
  });
  check(favs, { '自选股摘要 200': (r) => r.status === 200 });

  const quote = http.get(`${base}/api/v1/stocks/${symbol}`, {
    headers,
    tags: { name: 'stock_detail' },
  });
  check(quote, { '个股详情 200': (r) => r.status === 200 });

  const search = http.get(
    `${base}/api/v1/stocks/search?keyword=${encodeURIComponent(keyword)}`,
    { headers, tags: { name: 'search' } }
  );
  check(search, { '搜索 200': (r) => r.status === 200 });

  const status = http.get(`${base}/api/v1/market/status`, {
    headers,
    tags: { name: 'market_status' },
  });
  check(status, { '市场状态 200': (r) => r.status === 200 });
}
