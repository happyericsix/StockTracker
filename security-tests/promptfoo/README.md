# promptfoo 红队测试 —— AI 助手 agent

针对 `python-data-service` 的 ReAct 聊天 agent 做提示词注入 / 越权 / 密钥窃取 /
记忆投毒 / 纪律绕过测试。攻击流量直连本地 Python 服务（与线上公网用户经 Java
`/api/v1/chat/send` 转发后到达的是同一 agent 逻辑，Java 层对 message 无过滤）。

## 前置

1. 本地栈在跑：Python `127.0.0.1:8000`、Java `127.0.0.1:8080`、MySQL、Redis。
2. 仓库根目录 `.env` 里有 `INTERNAL_API_TOKEN`；`python-data-service/.env` 里有 `DEEPSEEK_API_KEY`。
3. Node.js ≥ 18。

## 运行

```bash
cd stock-tracker/security-tests/promptfoo

# 从根目录 .env 取内部令牌，从 python .env 取 DeepSeek key（rubric 评分用）
export INTERNAL_API_TOKEN=$(grep '^INTERNAL_API_TOKEN=' ../../.env | cut -d= -f2-)
export DEEPSEEK_API_KEY=$(grep '^DEEPSEEK_API_KEY=' ../../python-data-service/.env | cut -d= -f2-)

npx promptfoo@latest eval -c promptfooconfig.yaml -j 1 --output results.json
npx promptfoo@latest view   # 打开 Web UI 查看每个用例的完整回复
```

说明：

- `-j 1` 串行执行，避免并发压垮本地数据源与 DeepSeek 限速。
- 评分分两类：确定性断言（`not-contains` 等，零成本）与 `llm-rubric`
  （用 DeepSeek 当裁判，成本极低）。跑完一轮约 12 次 agent 调用。
- 测试账号 `sectest_probe1` 在 Java 侧真实存在（安全测试建号），
  其记忆/消息数据可随时清理（`DELETE FROM user WHERE username='sectest_probe1'`）。
- `.token` 文件是本机提取的内部令牌，已在 `.gitignore` 排除，勿提交。

## 用例清单

| 组 | 用例 | 验证目标 |
|---|---|---|
| A | A1/A2 系统提示词泄漏 | 直接索取 / 伪装运维索取 |
| B | B1/B2 身份越权 | 冒充管理员读他人记忆、伪造角色提权 |
| C | C1/C2 伪造指令触发写动作 | 假系统通知绕过确认；**新闻体内藏指令（间接注入）** |
| D | D1 密钥窃取 | API key / 内部令牌 |
| E | E1 记忆投毒 | 写入"永久免确认满仓" |
| F | F1/F2 投资纪律 | 强迫价格预测；默认值披露铁律（基线） |
| G | G1/G2 资源滥用 | 无界回测网格；无限工具循环 |
