"""Telegram alerting system."""
import threading
import time
from typing import Callable

from .config import Config, load_config

# Module state
_config: Config | None = None
_bot = None
_last_alert_time: float = 0
_alert_acknowledged: bool = False
_imbalance_counters: dict = {}


def init(config: Config | None = None):
    """Initialize the alerts module."""
    global _config, _bot
    
    _config = config or load_config()
    
    if _config.telegram_bot_token:
        try:
            import telebot
            _bot = telebot.TeleBot(_config.telegram_bot_token)
        except ImportError:
            print("⚠️ telebot not installed, Telegram alerts disabled")
        except Exception as e:
            print(f"Bot初始化失败: {e}")


def is_acknowledged() -> bool:
    return _alert_acknowledged


def acknowledge():
    """Acknowledge alerts to silence them."""
    global _alert_acknowledged
    _alert_acknowledged = True


def reset_acknowledgment():
    """Reset acknowledgment when conditions normalize."""
    global _alert_acknowledged
    _alert_acknowledged = False


def send_alert(message: str, force: bool = False) -> bool:
    """Send a Telegram alert message.
    
    Args:
        message: Alert text
        force: Bypass cooldown and acknowledgment
        
    Returns:
        True if message was sent
    """
    global _last_alert_time
    
    if not _bot or not _config.telegram_chat_id:
        return False
    
    if not force:
        if _alert_acknowledged:
            return False
        if time.time() - _last_alert_time < _config.alert_cooldown_s:
            return False
    
    try:
        _bot.send_message(_config.telegram_chat_id, message)
        _last_alert_time = time.time()
        return True
    except Exception as e:
        print(f"发送报警失败: {e}")
        return False


def check_position_alerts(var_positions: dict, para_positions: dict) -> list[str]:
    """Check positions for alert conditions.
    
    Args:
        var_positions: Dict of symbol -> {qty, upnl, ...}
        para_positions: Dict of symbol -> {qty, upnl, ...}
        
    Returns:
        List of alert messages
    """
    global _alert_acknowledged, _imbalance_counters
    
    alerts = []
    current_imbalanced = set()
    
    all_symbols = set(var_positions.keys()) | set(para_positions.keys())
    
    for symbol in all_symbols:
        v = var_positions.get(symbol, {})
        p = para_positions.get(symbol, {})
        
        v_qty = v.get("qty", 0.0)
        p_qty = p.get("qty", 0.0)
        net_qty = v_qty + p_qty
        
        v_upnl = v.get("upnl", 0.0)
        p_upnl = p.get("upnl", 0.0)
        
        # Check position imbalance
        if abs(net_qty) > _config.alert_threshold_net_qty:
            current_imbalanced.add(symbol)
            _imbalance_counters[symbol] = _imbalance_counters.get(symbol, 0) + 1
            if _imbalance_counters[symbol] >= _config.alert_imbalance_count:
                alerts.append(f"⚠️ {symbol} 头寸不平: Net {net_qty:.4f} (连续{_imbalance_counters[symbol]}次)")
        
        # UPNL alerts (immediate)
        if abs(v_upnl) > _config.alert_threshold_single_pos_upnl:
            alerts.append(f"🚨 {symbol} Var UPNL: {v_upnl:+.2f}")
        if abs(p_upnl) > _config.alert_threshold_single_pos_upnl:
            alerts.append(f"🚨 {symbol} Para UPNL: {p_upnl:+.2f}")
    
    # Clear counters for balanced symbols
    for symbol in list(_imbalance_counters.keys()):
        if symbol not in current_imbalanced:
            del _imbalance_counters[symbol]
    
    # Auto-reset acknowledgment when all clear
    if not alerts and _alert_acknowledged:
        _alert_acknowledged = False
    
    return alerts


def start_bot_polling(on_get: Callable | None = None, on_status: Callable | None = None):
    """Start Telegram bot polling in a background thread.
    
    Args:
        on_get: Callback when user sends /get command
        on_status: Callback when user sends /status command (should return status string)
    """
    if not _bot:
        return

    @_bot.message_handler(commands=["get"])
    def handle_get(message):
        global _alert_acknowledged
        _alert_acknowledged = True
        try:
            _bot.reply_to(message, "🔇 Alerts silenced until resolution.")
            if on_get:
                on_get()
        except Exception:
            pass

    @_bot.message_handler(commands=["status"])
    def handle_status(message):
        try:
            msg = on_status() if on_status else "No status available."
            _bot.reply_to(message, msg)
        except Exception:
            pass

    def run_polling():
        # Send startup notification
        try:
            if _config.telegram_chat_id:
                _bot.send_message(
                    _config.telegram_chat_id,
                    "🚀 Monitor Server Started.\nWaiting for data feed..."
                )
        except Exception:
            pass

        while True:
            try:
                _bot.polling(non_stop=True, interval=2)
            except Exception as e:
                print(f"Polling error: {e}")
                time.sleep(5)

    t = threading.Thread(target=run_polling, daemon=True)
    t.start()
