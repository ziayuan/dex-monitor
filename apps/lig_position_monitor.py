import http.server
import socketserver
import json
import logging
from datetime import datetime
import os
import sys
import threading
import time
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
import subprocess
import telebot
import asyncio
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")

# Debug Token
_token = os.getenv("VAR_LIG_TOKEN")
if _token:
    logger.info(f"Loaded Auth Token: {_token[:4]}...{_token[-4:]}")
else:
    logger.warning("No Auth Token loaded from .env!")

from exchanges.lighter import LighterClient

# Config
PORT = 8002  # Use 8002 to avoid conflict with bp_monitor(8001)
HOST = "127.0.0.1"
IMBALANCE_THRESHOLD_SEC = 10.0  # Alert after 10 seconds of sustained imbalance
NET_QTY_THRESHOLD = 0.001       # Very strict threshold
DATA_STALE_SEC = 10.0           # Data older than 10s is considered stale
WAKE_GRACE_SEC = 15.0           # Seconds to wait after waking from sleep

# Global State
VAR_POSITIONS = {}  # Symbol -> qty
LIG_POSITIONS = {}  # Symbol -> qty
LIG_CLIENT = None

# Data freshness tracking
LAST_VAR_UPDATE = 0.0   # timestamp of last Variational data received
LAST_LIG_UPDATE = 0.0   # timestamp of last Lighter data received
LAST_POLL_TIME = 0.0    # timestamp of last successful poll loop
WAKE_UNTIL = 0.0        # suppress alerts until this timestamp (after wake)

# Alert State
IMBALANCE_START_TIME = {} # Symbol -> timestamp
ALERT_COOLDOWN = 10
LAST_ALERT_TIME = 0
BOT_INSTANCE = None
ALERT_ACKNOWLEDGED = False

def init_bot():
    global BOT_INSTANCE
    try:
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        if not token and os.path.exists("config.json"):
            with open("config.json", "r") as f:
                cfg = json.load(f)
                token = cfg.get("telegram_bot_token")
        
        if token:
            BOT_INSTANCE = telebot.TeleBot(token)
            logger.info("Telegram Bot initialized")
        else:
            logger.warning("No Telegram token found in .env or config.json")
            
    except Exception as e:
        logger.error(f"Bot init failed: {e}")

def send_alert(msg: str):
    global LAST_ALERT_TIME
    
    # Sound Alert
    try:
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
    except:
        pass
        
    if time.time() - LAST_ALERT_TIME < ALERT_COOLDOWN:
        return
        
    logger.warning(f"🚨 ALERT: {msg}")
    
    # Telegram Alert
    try:
        if BOT_INSTANCE:
            chat_id = os.getenv("TELEGRAM_CHAT_ID")
            if not chat_id and os.path.exists("config.json"):
                 with open("config.json", "r") as f:
                    cfg = json.load(f)
                    chat_id = cfg.get("telegram_chat_id")
            
            if chat_id:
                 BOT_INSTANCE.send_message(chat_id, f"🚨 {msg}")
                 LAST_ALERT_TIME = time.time()
            else:
                 logger.warning("No Telegram Chat ID found")
    except Exception as e:
        logger.error(f"TG Send failed: {e}")

def check_imbalance():
    """Check for balance between Lighter and Var."""
    global IMBALANCE_START_TIME
    
    now = time.time()
    
    # --- Guard 1: Wake grace period ---
    if now < WAKE_UNTIL:
        IMBALANCE_START_TIME.clear()
        return
    
    # --- Guard 2: Data freshness check ---
    var_age = now - LAST_VAR_UPDATE if LAST_VAR_UPDATE > 0 else float('inf')
    lig_age = now - LAST_LIG_UPDATE if LAST_LIG_UPDATE > 0 else float('inf')
    
    if var_age > DATA_STALE_SEC or lig_age > DATA_STALE_SEC:
        # Data is stale, cannot reliably compare — suppress alerts
        IMBALANCE_START_TIME.clear()
        return
    
    all_symbols = set(VAR_POSITIONS.keys()) | set(LIG_POSITIONS.keys())
    
    for symbol in all_symbols:
        v_qty = VAR_POSITIONS.get(symbol, 0.0)
        l_qty = LIG_POSITIONS.get(symbol, 0.0)
        net = v_qty + l_qty
        
        if abs(net) > NET_QTY_THRESHOLD:
            # Imbalance detected
            if symbol not in IMBALANCE_START_TIME:
                IMBALANCE_START_TIME[symbol] = now
            else:
                duration = now - IMBALANCE_START_TIME[symbol]
                if duration > IMBALANCE_THRESHOLD_SEC:
                    send_alert(f"{symbol} Net Imbalance: {net:.4f} (Var:{v_qty}, Lig:{l_qty}) > {IMBALANCE_THRESHOLD_SEC}s")
        else:
            # Balanced
            if symbol in IMBALANCE_START_TIME:
                del IMBALANCE_START_TIME[symbol]

def print_dashboard():
    os.system('clear')
    print(f"=== Lighter-Variational Monitor ===")
    print(f"Time: {datetime.now().strftime('%H:%M:%S')}")
    print("-" * 50)
    print(f"{'Symbol':<10} | {'Var':<10} | {'Lig':<10} | {'Net':<10}")
    print("-" * 50)
    
    all_symbols = set(VAR_POSITIONS.keys()) | set(LIG_POSITIONS.keys())
    for s in sorted(all_symbols):
        v = VAR_POSITIONS.get(s, 0.0)
        l = LIG_POSITIONS.get(s, 0.0)
        net = v + l
        # Color output if imbalance
        if abs(net) > NET_QTY_THRESHOLD:
             line = f"{s:<10} | {v:<10.4f} | {l:<10.4f} | {net:<10.4f}  <-- IMBALANCE"
        else:
             line = f"{s:<10} | {v:<10.4f} | {l:<10.4f} | {net:<10.4f}"
        print(line)
        
    print("-" * 50)
    
    # Check alert
    check_imbalance()

class VarRequestHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        return

    def do_OPTIONS(self):
        self.send_response(200, "ok")
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token")
        self.end_headers()

    def do_POST(self):
        if self.path == '/update':
            # print("DEBUG: Received POST request") # Uncomment to see every heartbeat
            
            # Authentication
            token = os.getenv("VAR_LIG_TOKEN")
            req_token = self.headers.get("X-Auth-Token")
            
            # Debug logs
            # logger.info(f"Received POST. Token env: {token}, Header: {req_token}")
            
            if not token or req_token != token:
                logger.warning(f"Unauthorized access attempt. Exp: {token}, Got: {req_token}")
                self.send_response(403)
                self.end_headers()
                return

            try:
                content_length = int(self.headers['Content-Length'])
                post_data = self.rfile.read(content_length)
                # logger.info(f"Received {content_length} bytes")
                
                raw_txt = post_data.decode('utf-8')
                if raw_txt.startswith('"') and raw_txt.endswith('"'):
                    raw_txt = json.loads(raw_txt)
                
                data = json.loads(raw_txt)
                self.process_data(data)
                
                self.send_response(200)
                self.send_header('Access-Control-Allow-Origin', '*')
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
            except Exception as e:
                logger.error(f"Error parsing POST: {e}")
                self.send_response(400)
                self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    def process_data(self, data):
        global VAR_POSITIONS, LAST_VAR_UPDATE
        # logger.info(f"Processing data keys: {list(data.keys())}")
        if "positions" in data:
            if isinstance(data["positions"], list):
                # Update Var positions
                # Assuming full snapshot from Var source
                VAR_POSITIONS.clear()
                for p in data["positions"]:
                    info = p.get("position_info", {})
                    symbol = info.get("instrument", {}).get("underlying", "UNKNOWN")
                    qty = float(info.get("qty", 0))
                    # Adjust sign if needed based on delta or logic
                    price_info = p.get("price_info", {})
                    delta = float(price_info.get("delta", 0))
                    if delta < 0:
                        qty = -qty
                    VAR_POSITIONS[symbol] = qty
                LAST_VAR_UPDATE = time.time()
                # logger.info(f"Updated positions: {VAR_POSITIONS}")
                print_dashboard()
            else:
                 logger.warning(f"'positions' is not a list: {type(data['positions'])}")
        else:
             pass 
             # logger.warning("No 'positions' key in data")

def poll_lighter():
    global LIG_POSITIONS, LAST_POLL_TIME, WAKE_UNTIL, LAST_LIG_UPDATE, IMBALANCE_START_TIME
    
    # Init client
    pub_k = os.getenv("LIGHTER_PUBLIC_KEY")
    priv_k = os.getenv("LIGHTER_PRIVATE_KEY")
    api_idx = int(os.getenv("LIGHTER_API_INDEX", "0"))
    l1_addr = os.getenv("LIGHTER_L1_ADDRESS")
    
    if not pub_k and os.path.exists("config.json"):
        with open("config.json", "r") as f:
            cfg = json.load(f)
            pub_k = cfg.get("lighter_public_key")
            priv_k = cfg.get("lighter_private_key")
            # l1 address might not be in config if user just added it to env
            if not l1_addr:
                 l1_addr = cfg.get("lighter_l1_address")
    
    client = LighterClient(public_key=pub_k, private_key=priv_k, api_index=api_idx, l1_address=l1_addr)
    
    logger.info("Started Lighter polling...")
    while True:
        try:
            # --- Sleep/wake detection ---
            now = time.time()
            if LAST_POLL_TIME > 0 and (now - LAST_POLL_TIME) > 5.0:
                WAKE_UNTIL = now + WAKE_GRACE_SEC
                logger.warning(f"⏰ Detected wake from sleep (gap={now - LAST_POLL_TIME:.1f}s). Suppressing alerts for {WAKE_GRACE_SEC}s")
                IMBALANCE_START_TIME.clear()
            LAST_POLL_TIME = now
            
            # Fetch async
            positions = asyncio.run(client.get_positions())
            LIG_POSITIONS = positions
            LAST_LIG_UPDATE = time.time()
            print_dashboard()
        except Exception as e:
            # logger.error(f"Lighter poll error: {e}")
            pass
        
        time.sleep(1)

class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True

def run_server():
    print(f"🚀 Monitor listening on http://{HOST}:{PORT}")
    with ReusableTCPServer((HOST, PORT), VarRequestHandler) as httpd:
        httpd.serve_forever()

if __name__ == "__main__":
    init_bot()
    
    # Start Lighter poller
    t = threading.Thread(target=poll_lighter, daemon=True)
    t.start()
    
    # Start HTTP server
    try:
        run_server()
    except KeyboardInterrupt:
        pass
