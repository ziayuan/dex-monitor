import http.server
import socketserver
import json
import logging
from datetime import datetime
import os

# 配置
PORT = 8001
HOST = "127.0.0.1"

logging.basicConfig(level=logging.ERROR, format='%(asctime)s - %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("VarServer")

# 全局状态
VAR_POSITIONS = {}  # Symbol -> {qty, entry, mark, upnl}
PARA_POSITIONS = {} # Symbol -> {qty, entry, mark, upnl}
VAR_BALANCE = {"balance": 0, "upnl": 0, "im": 0}

import threading
import telebot
import time
import requests

# --- Alerting Config ---
LAST_ALERT_TIME = 0
ALERT_COOLDOWN = 1  # 持续报警间隔 30秒
ALERT_ACKNOWLEDGED = False # 是否已确认/静音
BOT_INSTANCE = None # 全局 Bot 对象

# 连续不平衡计数器 (防止下单延迟误报)
IMBALANCE_COUNTERS = {}  # Symbol -> consecutive count
IMBALANCE_THRESHOLD = 12  # 连续N次检测到才报警

def init_bot():
    global BOT_INSTANCE
    try:
        if os.path.exists("config.json"):
            with open("config.json", "r") as f:
                cfg = json.load(f)
                token = cfg.get("telegram_bot_token")
                if token:
                    BOT_INSTANCE = telebot.TeleBot(token)
    except Exception as e:
        logger.error(f"Bot初始化失败: {e}")

def start_telegram_bot():
    if not BOT_INSTANCE:
        return

    @BOT_INSTANCE.message_handler(commands=['get'])
    def handle_get(message):
        global ALERT_ACKNOWLEDGED
        ALERT_ACKNOWLEDGED = True
        try:
            BOT_INSTANCE.reply_to(message, "🔇 Alerts silenced until resolution.")
            logger.info("用户发送 /get，报警已静音")
        except:
            pass

    @BOT_INSTANCE.message_handler(commands=['status'])
    def handle_status(message):
        msg = format_status_message()
        try:
            BOT_INSTANCE.reply_to(message, msg)
        except:
            pass

    def run_polling():
        # 发送启动通知
        try:
            chat_id = None
            if os.path.exists("config.json"):
                with open("config.json", "r") as f:
                    cfg = json.load(f)
                    chat_id = cfg.get("telegram_chat_id")
            
            if BOT_INSTANCE and chat_id:
                BOT_INSTANCE.send_message(chat_id, "🚀 Monitor Server Started.\nWaiting for data feed...")
        except:
            pass
            
        while True:
            try:
                logger.info("启动 Telegram Polling...")
                BOT_INSTANCE.polling(non_stop=True, interval=2)
            except Exception as e:
                logger.error(f"Polling error: {e}")
                time.sleep(5)

    t = threading.Thread(target=run_polling, daemon=True)
    t.start()

def format_status_message():
    all_symbols = set(VAR_POSITIONS.keys()) | set(PARA_POSITIONS.keys())
    if not all_symbols:
        return "No data available."

    # Header
    lines = []
    lines.append(f"💰 Balance: {VAR_BALANCE['balance']:,.2f}")
    lines.append(f"🛡️ IM: {VAR_BALANCE['im']:,.2f}")
    lines.append("-" * 20)

    total_net_pnl = 0.0
    
    for symbol in sorted(all_symbols):
        v = VAR_POSITIONS.get(symbol, {})
        p = PARA_POSITIONS.get(symbol, {})
        
        v_qty = v.get("qty", 0.0)
        p_qty = p.get("qty", 0.0)
        net_qty = v_qty + p_qty
        
        v_upnl = v.get("upnl", 0.0)
        p_upnl = p.get("upnl", 0.0)
        net_pnl = v_upnl + p_upnl
        total_net_pnl += net_pnl
        
        # Mobile friendly block
        # 🔹 Symbol
        # V: 100 | P: -100
        # Net: 0 | PnL: +50.0
        lines.append(f"🔹 *{symbol}*")
        lines.append(f"   Var: {v_qty} | Para: {p_qty}")
        lines.append(f"   Net: {net_qty:.4f} | PnL: {net_pnl:+.2f}")
        lines.append("") # Empty line separator
    
    lines.append("-" * 20)
    lines.append(f"💵 *Total Net UPNL*: {total_net_pnl:+.2f}")
    
    return "\n".join(lines)

def send_telegram_alert(msg):
    global LAST_ALERT_TIME
    
    # 如果已确认，则不再发送
    if ALERT_ACKNOWLEDGED:
        return

    # 动态读取冷却配置
    cooldown = ALERT_COOLDOWN
    try:
        if os.path.exists("config.json"):
            with open("config.json", "r") as f:
                cfg = json.load(f)
                cooldown = int(cfg.get("alert_cooldown_s", ALERT_COOLDOWN))
    except:
        pass

    # CD 检查
    if time.time() - LAST_ALERT_TIME < cooldown:
        return
    
    try:
        # 读取 Chat ID
        chat_id = None
        if os.path.exists("config.json"):
            with open("config.json", "r") as f:
                cfg = json.load(f)
                chat_id = cfg.get("telegram_chat_id")
        
        if BOT_INSTANCE and chat_id:
            try:
                BOT_INSTANCE.send_message(chat_id, msg)
                logger.info(f"📢 已发送报警: {msg}")
                LAST_ALERT_TIME = time.time()
            except Exception as send_err:
                logger.error(f"发送消息API报错: {send_err}")
    except Exception as e:
        logger.error(f"发送报警失败: {e}")

def check_alerts():
    global ALERT_ACKNOWLEDGED, IMBALANCE_COUNTERS
    
    # 动态读取配置
    net_qty_thresh = 0.01
    single_pos_thresh = 3000.0
    imbalance_threshold = IMBALANCE_THRESHOLD
    
    try:
        if os.path.exists("config.json"):
            with open("config.json", "r") as f:
                cfg = json.load(f)
                net_qty_thresh = float(cfg.get("alert_threshold_net_qty", 0.01))
                single_pos_thresh = float(cfg.get("alert_threshold_single_pos_upnl", 3000.0))
                imbalance_threshold = int(cfg.get("alert_imbalance_count", IMBALANCE_THRESHOLD))  # 可配置连续检测次数
    except:
        pass

    alerts = []
    current_imbalanced = set()  # 本次检测到的不平衡 symbol
    
    all_symbols = set(VAR_POSITIONS.keys()) | set(PARA_POSITIONS.keys())
    for symbol in all_symbols:
        v = VAR_POSITIONS.get(symbol, {})
        p = PARA_POSITIONS.get(symbol, {})
        
        v_qty = v.get("qty", 0.0)
        p_qty = p.get("qty", 0.0)
        net_qty = v_qty + p_qty
        
        v_upnl = v.get("upnl", 0.0)
        p_upnl = p.get("upnl", 0.0)
        
        # 检查头寸不平衡
        if abs(net_qty) > net_qty_thresh:
            current_imbalanced.add(symbol)
            # 增加连续计数
            IMBALANCE_COUNTERS[symbol] = IMBALANCE_COUNTERS.get(symbol, 0) + 1
            # 只有达到阈值才报警
            if IMBALANCE_COUNTERS[symbol] >= imbalance_threshold:
                alerts.append(f"⚠️ {symbol} 头寸不平: Net {net_qty:.4f} (连续{IMBALANCE_COUNTERS[symbol]}次)")
        
        # UPNL 报警 (这些不需要连续检测，立即报)
        if abs(v_upnl) > single_pos_thresh:
             alerts.append(f"🚨 {symbol} Var UPNL: {v_upnl:+.2f}")
        if abs(p_upnl) > single_pos_thresh:
             alerts.append(f"🚨 {symbol} Para UPNL: {p_upnl:+.2f}")
    
    # 清除已恢复平衡的 symbol 的计数器
    for symbol in list(IMBALANCE_COUNTERS.keys()):
        if symbol not in current_imbalanced:
            del IMBALANCE_COUNTERS[symbol]

    if alerts:
        # 有报警状态: 尝试发送
        send_telegram_alert("\n".join(alerts))
    else:
        # 无报警状态: 自动恢复确认标记，以便下次出问题能再次报警
        if ALERT_ACKNOWLEDGED:
            ALERT_ACKNOWLEDGED = False
            logger.info(" ✅ 报警解除，重置静音状态")


def print_dashboard():
    # 合并所有涉及的 Symbol
    all_symbols = set(VAR_POSITIONS.keys()) | set(PARA_POSITIONS.keys())
    
    timestamp = datetime.now().strftime('%H:%M:%S')
    
    print(f"\n[{timestamp}] 💰 Var余额: {VAR_BALANCE['balance']:,.2f} | IM: {VAR_BALANCE['im']:,.2f}")
    print("=" * 85)
    print(f"{'Symbol':<8} | {'Var Qty':<10} | {'Para Qty':<10} | {'Net Qty':<10} | {'Net PNL':<10}")
    print("-" * 85)
    
    total_net_pnl = 0
    
    for symbol in sorted(all_symbols):
        v = VAR_POSITIONS.get(symbol, {})
        p = PARA_POSITIONS.get(symbol, {})
        
        v_qty = v.get("qty", 0.0)
        p_qty = p.get("qty", 0.0)
        net_qty = v_qty + p_qty
        
        v_upnl = v.get("upnl", 0.0)
        p_upnl = p.get("upnl", 0.0)
        net_pnl = v_upnl + p_upnl
        total_net_pnl += net_pnl
        
        print(f"{symbol:<8} | {v_qty:<10.4f} | {p_qty:<10.4f} | {net_qty:<10.4f} | {net_pnl:+.2f}")
    print("-" * 85)
    print(f"Total Net UPNL: {total_net_pnl:+.2f}")
    print("=" * 85)
    
    # 检查报警
    check_alerts()

class VarRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        if self.path == '/update':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                # 某些情况下可能是 str，需要 double decode?
                # 通常 fetch body 是 stringified json
                raw_txt = post_data.decode('utf-8')
                # 有时候传来的是双重 JSON 字符串
                if raw_txt.startswith('"') and raw_txt.endswith('"'):
                    raw_txt = json.loads(raw_txt)
                
                data = json.loads(raw_txt)
                self.process_data(data)
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                logger.error(f"解析出错: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return

    def process_data(self, data):
        global VAR_POSITIONS, PARA_POSITIONS, VAR_BALANCE
        # Debug: 打印收到的任何数据概要
        keys = list(data.keys())
        if "method" in data: 
             # 只有当是 paramdex 相关时才大声打印，避免 var 刷屏
             print(f"DEBUG: Data Method: {data.get('method')}, Channel: {data.get('params',{}).get('channel')}")
        
        need_print = False

        # --- Variational Data Handling ---
        if "pool_portfolio_result" in data:
            res = data["pool_portfolio_result"]
            VAR_BALANCE["balance"] = float(res.get("balance", 0))
            VAR_BALANCE["im"] = float(res.get("margin_usage", {}).get("initial_margin", 0))
            need_print = True

        if "positions" in data and isinstance(data["positions"], list):
            # 这是一个 Variational 的全量/增量 推送
            # 如果是全量，应该重置？WS 通常给的是当前快照吗？
            # 假设是快照，先清空 (针对该 Symbol? 不，Variational 是全账户推送)
            # 简单起见，我们假设 WS 推送的是所有持仓的列表 (Variational 确实如此)
            
            # 清空旧的 Variational 持仓 (防止平仓后还残留)
            VAR_POSITIONS = {}
            
            for p in data["positions"]:
                info = p.get("position_info", {})
                price_info = p.get("price_info", {})
                
                symbol = info.get("instrument", {}).get("underlying", "UNKNOWN")
                qty = float(info.get("qty", 0))
                delta = float(price_info.get("delta", 0))
                # 修正方向: Variational quantity总是正数，方向看 delta 或者 logic
                # 如果 delta < 0, qty 应当为负
                if delta < 0:
                    qty = -qty
                
                VAR_POSITIONS[symbol] = {
                    "qty": qty,
                    "entry": float(info.get("avg_entry_price", 0)),
                    "mark": float(price_info.get("price", 0)),
                    "upnl": float(p.get("upnl", 0))
                }
            need_print = True

        # --- Paradex Data Handling ---
        # 结构: { method: "subscription", params: { channel: "positions", data: {...} } }
        if data.get("method") == "subscription" and data.get("params", {}).get("channel") == "positions":
            p_data = data["params"]["data"]
            # Paradex 推送是单个 Position 更新，不是全量快照
            # 所以我们更新字典，不能直接覆盖/清空
            
            market = p_data.get("market", "") # e.g. "BNB-USD-PERP"
            symbol = market.split("-")[0] # "BNB"
            
            size = float(p_data.get("size", 0))
            upnl = float(p_data.get("unrealized_pnl", 0))
            entry_price = float(p_data.get("average_entry_price", 0))
            
            # 用户要求不反推价格，直接置 0
            mark_price = 0.0

            if p_data.get("status") == "CLOSED" or size == 0:
                if symbol in PARA_POSITIONS:
                    del PARA_POSITIONS[symbol]
            else:
                PARA_POSITIONS[symbol] = {
                    "qty": size,
                    "entry": entry_price,
                    "mark": mark_price, 
                    "upnl": upnl
                }
            
            need_print = True

        if need_print:
            print_dashboard()

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

if __name__ == "__main__":
    init_bot() # 初始化 Bot
    start_telegram_bot() # 启动监听线程

    print(f"🚀 综合监控服务器已启动: http://{HOST}:{PORT}")
    print("等待 Variational 和 Paradex 数据...")
    with ReusableTCPServer((HOST, PORT), VarRequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 服务器已停止")
