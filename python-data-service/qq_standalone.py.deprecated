import asyncio, json, logging, os, threading, requests, subprocess, time
from collections import defaultdict
from datetime import datetime
from aiocqhttp import CQHttp
from http.server import HTTPServer, BaseHTTPRequestHandler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

YOUR_WEBHOOK = "http://localhost:8000/qq_msg"
bot = CQHttp()
send_queues = defaultdict(list)

MYSQL_PATH = "E:\\MySQL\\MySQL Server 8.0\\bin\\mysql.exe"
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "123456")
MYSQL_ARGS = ["-u", "root", f"-p{MYSQL_PASSWORD}", "stockdb", "--batch", "--skip-column-names"]



# 调腾讯API直接拿完整数据（名称、现价、涨跌幅、最高、最低）
def get_stock_data(symbol):
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        code = symbol.strip().upper().replace('SH','').replace('SZ','').replace('HK','').replace('US','')
        if code.startswith('6'): p = 'sh'
        elif code.startswith('0') or code.startswith('3') or code.startswith('2'): p = 'sz'
        elif code.startswith('8') or code.startswith('4') or code.startswith('92'): p = 'bj'
        else: p = ''
        url = f'http://qt.gtimg.cn/q={p}{code}'
        resp = requests.get(url, headers=headers, timeout=5)
        resp.encoding = 'gbk'
        parts = resp.text.split('~')
        if len(parts) < 40: return None
        return {
            'name': parts[1],
            'price': parts[3],
            '涨跌幅': parts[32],
            '最高': parts[33],
            '最低': parts[34],
        }
    except Exception as e:
        logger.error(f"查价失败 {symbol}: {e}")
        return None


def get_qq_users():
    try:
        cmd = [MYSQL_PATH] + MYSQL_ARGS + ["-e", "SELECT u.id, u.qq_number, GROUP_CONCAT(CONCAT(fs.stock_symbol, '|', COALESCE(fs.buy_price, ''), '|', COALESCE(fs.quantity, ''))) FROM users u LEFT JOIN favorite_stocks fs ON u.id = fs.user_id WHERE u.qq_number IS NOT NULL AND u.qq_number != '' GROUP BY u.id, u.qq_number"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        users = []
        for line in result.stdout.strip().split("\n"):
            if line.strip():
                parts = line.split("\t")
                favs = []
                if len(parts) > 2 and parts[2]:
                    for item in parts[2].split(","):
                        segs = item.split("|")
                        favs.append({"symbol": segs[0], "buy_price": segs[1] if len(segs) > 1 else "", "quantity": segs[2] if len(segs) > 2 else ""})
                users.append({"qq_number": parts[1], "favorites": favs})
        return users
    except Exception as e:
        logger.error(f"查询数据库失败: {e}")
        return []


def build_report(favorites):
    msg = "🌅 早安，今日自选股速报：\n\n"
    for fav in favorites:
        sym = fav["symbol"] if isinstance(fav, dict) else fav
        data = get_stock_data(sym)
        if not data:
            msg += f"【{sym}】查询失败\n"
            continue

        name = data.get("name", sym)
        msg += f"【{name}({sym})】\n"
        if isinstance(fav, dict) and fav.get("buy_price"):
            cost = float(fav["buy_price"])
            curr = float(data["price"])
            pnl = ((curr - cost) / cost) * 100
            arrow = "📈" if pnl >= 0 else "📉"
            msg += f"  成本: ¥{cost}    现价: ¥{curr}    {arrow} {pnl:+.1f}%\n"
        else:
            price = data.get("price", "?")
            chg = data.get("涨跌幅", "?")
            msg += f"  现价: ¥{price}    涨跌: {chg}%\n"
        high = data.get("最高", "?")
        low = data.get("最低", "?")
        msg += f"  最高: ¥{high}    最低: ¥{low}\n"

        # 量化分析
        try:
            url = f"http://127.0.0.1:8000/api/v1/indicators/{sym}"
            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                q = resp.json()
                if "signal" in q and "error" not in q:
                    sig = q["signal"]["signal"]
                    score = q["signal"]["score"]
                    msg += f"  📊 信号: {sig} (评分:{score:+d})\n"
                    if q.get("prediction"):
                        pred = q["prediction"]["predicted_change_pct"]
                        msg += f"  🔮 预测: {pred:+.2f}%\n"
        except Exception as e:
            logger.debug(f"量化分析获取失败 {sym}: {e}")

        msg += "\n"
    msg += "\n💡 LLM 分析即将上线……"
    return msg


def daily_push_loop():
    while True:
        now = datetime.now()
        if now.hour == 8 and now.minute == 30 and now.second < 5:
            logger.info("开始执行每日推送...")
            users = get_qq_users()
            logger.info(f"找到 {len(users)} 个绑定了QQ的用户")
            for u in users:
                if not u["favorites"]:
                    continue
                try:
                    msg = build_report(u["favorites"])
                    qq = int(u["qq_number"])
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(bot.send_private_msg(user_id=qq, message=msg))
                    loop.close()
                    logger.info(f"已推送 {qq}")
                except Exception as e:
                    logger.error(f"推送 {u['qq_number']} 失败: {e}")
            time.sleep(60)
        time.sleep(1)


class SendHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        send_queues[data["user_id"]].append(data)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
    log_message = lambda s,*a: None

@bot.on_startup
async def init():
    t = threading.Thread(target=lambda: HTTPServer(("0.0.0.0", 8081), SendHandler).serve_forever(), daemon=True)
    t.start()
    logger.info("发送接口 http://0.0.0.0:8081")
    logger.info("等待 NapCat 连接...")
    t2 = threading.Thread(target=daily_push_loop, daemon=True)
    t2.start()
    logger.info("每日推送线程已启动")

@bot.on_message()
async def handle_msg(event):
    user_id = event.user_id
    raw = event.message
    text = " ".join(s["data"]["text"] for s in raw if s.get("type") == "text") if isinstance(raw, list) else str(raw).strip()
    logger.info(f"收到 [{user_id}]: {text}")
    if text in ["测试日报", "推送测试", "日报测试"]:
        logger.info("手动触发每日推送...")
        users = get_qq_users()
        for u in users:
            if not u["favorites"]: continue
            msg = build_report(u["favorites"])
            await bot.send_private_msg(user_id=int(u["qq_number"]), message=msg)
        await bot.send_private_msg(user_id=user_id, message="✅ 日报推送完成")
        return
    try:
        await asyncio.to_thread(requests.post, YOUR_WEBHOOK, json={"user_id": user_id, "message": text}, timeout=3)
    except Exception as e:
        logger.error(f"转发失败: {e}")
    my_queue = send_queues.get(user_id, [])
    while my_queue:
        item = my_queue.pop(0)
        try:
            await bot.send_private_msg(user_id=item["user_id"], message=item["message"])
            logger.info(f"已发送")
        except Exception as e:
            logger.error(f"发送失败: {e}")

bot.run(host="0.0.0.0", port=3003)