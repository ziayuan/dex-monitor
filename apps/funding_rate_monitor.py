"""
Funding Rate & Price Monitor: Lighter vs TradeXYZ (Hyperliquid HIP-3)

统一监控系统：支持资金费率监控和价格范围监控。
每个监控项有唯一 ID，可通过 Telegram Bot 动态管理。

Usage:
    python apps/funding_rate_monitor.py
"""

import asyncio
import os
import sys
import time
import threading
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from dotenv import load_dotenv

load_dotenv()

from exchanges.lighter import LighterClient
from exchanges.hyperliquid import HyperliquidClient

# ──────────────────────────────────────
# Symbol mapping: Lighter ⟷ TradeXYZ
# ──────────────────────────────────────
SYMBOL_MAP = {
    "EURUSD": "EUR", "USDJPY": "JPY", "GBPUSD": "GBP",
    "AUDUSD": "AUD", "USDCAD": "CAD", "USDCHF": "CHF",
    "NZDUSD": "NZD", "USDKRW": "KRW",
}

# ──────────────────────────────────────
# Configuration
# ──────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
MONITOR_PAIRS_STR = os.getenv("FUNDING_MONITOR_PAIRS", "EURUSD,USDJPY")
POLL_INTERVAL = int(os.getenv("FUNDING_POLL_INTERVAL", "300"))
PROXY = os.getenv("FUNDING_PROXY", os.getenv("var_http_proxy", "http://127.0.0.1:7897"))
MONITOR_PAIRS = [p.strip() for p in MONITOR_PAIRS_STR.split(",") if p.strip()]

CST = timezone(timedelta(hours=8))

# Lighter 的资金费率是 8 小时周期，TradeXYZ 是 1 小时周期
# 统一换算成小时费率进行对比
LIGHTER_FUNDING_HOURS = 8


# ──────────────────────────────────────
# Monitor Item
# ──────────────────────────────────────
@dataclass
class MonitorItem:
    id: int
    type: str  # "funding_rate" or "price_range"
    symbol: str  # Lighter-side symbol (e.g. EURUSD, USDJPY)
    enabled: bool = True
    # funding_rate params
    alert_dir: str = "gt"  # "gt" = Lighter > XYZ 时报警, "lt" = Lighter < XYZ 时报警
    # price_range params
    price_low: Optional[float] = None
    price_high: Optional[float] = None
    # cooldown
    last_alert_time: float = 0.0
    alert_cooldown: float = 300.0  # 5 min

    def describe(self) -> str:
        status = "🟢" if self.enabled else "🔴"
        if self.type == "funding_rate":
            xyz_sym = SYMBOL_MAP.get(self.symbol, self.symbol)
            dir_str = "L>X" if self.alert_dir == "gt" else "L<X"
            return f"{status} #{self.id} 资金费率 | {self.symbol} (Lighter vs TradeXYZ/{xyz_sym}) [{dir_str}]"
        elif self.type == "price_range":
            return f"{status} #{self.id} 价格范围 | {self.symbol} @ Lighter | 安全区间: {self.price_low}~{self.price_high}"
        return f"{status} #{self.id} {self.type} | {self.symbol}"

    def can_alert(self) -> bool:
        return self.enabled and (time.time() - self.last_alert_time >= self.alert_cooldown)

    def mark_alerted(self):
        self.last_alert_time = time.time()


class FundingRateMonitor:
    """Unified monitor for funding rates and price ranges."""

    def __init__(self):
        self.lighter = LighterClient()
        self.hyperliquid = HyperliquidClient(proxy=PROXY)
        self.poll_interval = POLL_INTERVAL
        self.bot = None
        self._running = True
        self._next_id = 1

        # Monitor list
        self.monitors: list[MonitorItem] = []

        # Data caches
        self.latest_lighter_rates = {}
        self.latest_xyz_rates = {}
        self.latest_prices = {}

        # Create default funding rate monitors
        for sym in MONITOR_PAIRS:
            self._add_monitor("funding_rate", sym)

        self._init_telegram()

    def _add_monitor(self, mtype: str, symbol: str,
                     price_low: float = None, price_high: float = None) -> MonitorItem:
        """Add a new monitor and return it."""
        m = MonitorItem(
            id=self._next_id,
            type=mtype,
            symbol=symbol.upper(),
            price_low=price_low,
            price_high=price_high,
        )
        self._next_id += 1
        self.monitors.append(m)
        return m

    def _get_monitor(self, mid: int) -> Optional[MonitorItem]:
        for m in self.monitors:
            if m.id == mid:
                return m
        return None

    # ──────────────────────────────────────
    # Telegram
    # ──────────────────────────────────────
    def _init_telegram(self):
        if TELEGRAM_BOT_TOKEN:
            try:
                import telebot
                self.bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)
                self._register_commands()
                logger.info("Telegram bot initialized")
            except ImportError:
                logger.warning("telebot not installed")
            except Exception as e:
                logger.error(f"Telegram init failed: {e}")

    def _register_commands(self):
        if not self.bot:
            return

        @self.bot.message_handler(commands=["list"])
        def handle_list(message):
            """Show all monitors with IDs."""
            try:
                if not self.monitors:
                    self.bot.reply_to(message, "📋 暂无监控项")
                    return
                lines = ["📋 *监控列表*", ""]
                for m in self.monitors:
                    lines.append(m.describe())
                lines.append("")
                lines.append("_命令: /stop N, /start N, /rm N_")
                self.bot.reply_to(message, "\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                self.bot.reply_to(message, f"❌ {e}")

        @self.bot.message_handler(commands=["stop"])
        def handle_stop(message):
            """Stop a monitor. Usage: /stop 1"""
            try:
                parts = message.text.split()
                if len(parts) < 2:
                    self.bot.reply_to(message, "用法: /stop <ID>\n发 /list 查看所有 ID")
                    return
                mid = int(parts[1])
                m = self._get_monitor(mid)
                if not m:
                    self.bot.reply_to(message, f"❌ 未找到 ID #{mid}")
                    return
                m.enabled = False
                self.bot.reply_to(message, f"🔴 已停止 #{mid}: {m.describe()}")
            except ValueError:
                self.bot.reply_to(message, "❌ ID 必须是数字")

        @self.bot.message_handler(commands=["start"])
        def handle_start(message):
            """Start a stopped monitor. Usage: /start 1"""
            try:
                parts = message.text.split()
                if len(parts) < 2:
                    self.bot.reply_to(message, "用法: /start <ID>")
                    return
                mid = int(parts[1])
                m = self._get_monitor(mid)
                if not m:
                    self.bot.reply_to(message, f"❌ 未找到 ID #{mid}")
                    return
                m.enabled = True
                self.bot.reply_to(message, f"🟢 已启动 #{mid}: {m.describe()}")
            except ValueError:
                self.bot.reply_to(message, "❌ ID 必须是数字")

        @self.bot.message_handler(commands=["rm"])
        def handle_rm(message):
            """Remove a monitor. Usage: /rm 1"""
            try:
                parts = message.text.split()
                if len(parts) < 2:
                    self.bot.reply_to(message, "用法: /rm <ID>")
                    return
                mid = int(parts[1])
                m = self._get_monitor(mid)
                if not m:
                    self.bot.reply_to(message, f"❌ 未找到 ID #{mid}")
                    return
                self.monitors.remove(m)
                self.bot.reply_to(message, f"🗑️ 已删除 #{mid}: {m.describe()}")
            except ValueError:
                self.bot.reply_to(message, "❌ ID 必须是数字")

        @self.bot.message_handler(commands=["addprice"])
        def handle_addprice(message):
            """Add price range monitor. Usage: /addprice USDJPY 153 159"""
            try:
                parts = message.text.split()
                if len(parts) < 4:
                    self.bot.reply_to(
                        message,
                        "用法: /addprice <合约> <下限> <上限>\n"
                        "例: /addprice USDJPY 153 159\n"
                        "在安全区间内不报警,超出区间时报警"
                    )
                    return
                symbol = parts[1].upper()
                low = float(parts[2])
                high = float(parts[3])
                if low >= high:
                    self.bot.reply_to(message, "❌ 下限必须小于上限")
                    return
                m = self._add_monitor("price_range", symbol, price_low=low, price_high=high)
                self.bot.reply_to(message, f"✅ 已添加: {m.describe()}")
            except ValueError:
                self.bot.reply_to(message, "❌ 价格必须是数字")

        @self.bot.message_handler(commands=["addfunding"])
        def handle_addfunding(message):
            """Add funding rate monitor. Usage: /addfunding EURUSD"""
            try:
                parts = message.text.split()
                if len(parts) < 2:
                    self.bot.reply_to(
                        message,
                        "用法: /addfunding <合约>\n"
                        f"可选: {', '.join(SYMBOL_MAP.keys())}"
                    )
                    return
                symbol = parts[1].upper()
                if symbol not in SYMBOL_MAP:
                    self.bot.reply_to(message, f"❌ 不支持 {symbol}\n可选: {', '.join(SYMBOL_MAP.keys())}")
                    return
                m = self._add_monitor("funding_rate", symbol)
                self.bot.reply_to(message, f"✅ 已添加: {m.describe()}")
            except Exception as e:
                self.bot.reply_to(message, f"❌ {e}")

        @self.bot.message_handler(commands=["funding"])
        def handle_funding(message):
            """Show current funding rate and price data."""
            try:
                msg = self._format_status()
                self.bot.reply_to(message, msg, parse_mode="Markdown")
            except Exception as e:
                self.bot.reply_to(message, f"❌ {e}")

        @self.bot.message_handler(commands=["interval"])
        def handle_interval(message):
            try:
                parts = message.text.split(maxsplit=1)
                if len(parts) < 2:
                    self.bot.reply_to(message, f"当前轮询间隔: {self.poll_interval}s\n用法: /interval 300")
                    return
                val = int(parts[1].strip())
                if val < 30:
                    self.bot.reply_to(message, "⚠️ 最小 30 秒")
                    return
                self.poll_interval = val
                self.bot.reply_to(message, f"✅ 轮询间隔 → {val}s")
            except ValueError:
                self.bot.reply_to(message, "❌ 请输入数字")

        @self.bot.message_handler(commands=["setdir"])
        def handle_setdir(message):
            """Set alert direction for a funding monitor. Usage: /setdir 1 gt or /setdir 1 lt"""
            try:
                parts = message.text.split()
                if len(parts) < 3:
                    self.bot.reply_to(
                        message,
                        "用法: /setdir <ID> <gt|lt>\n"
                        "  gt = Lighter > TradeXYZ 时报警\n"
                        "  lt = Lighter < TradeXYZ 时报警"
                    )
                    return
                mid = int(parts[1])
                direction = parts[2].lower()
                if direction not in ("gt", "lt"):
                    self.bot.reply_to(message, "❌ 方向只能是 gt 或 lt")
                    return
                m = self._get_monitor(mid)
                if not m:
                    self.bot.reply_to(message, f"❌ 未找到 ID #{mid}")
                    return
                if m.type != "funding_rate":
                    self.bot.reply_to(message, f"❌ #{mid} 不是资金费率监控")
                    return
                m.alert_dir = direction
                dir_str = "Lighter > TradeXYZ" if direction == "gt" else "Lighter < TradeXYZ"
                self.bot.reply_to(message, f"✅ #{mid} 报警方向 → {dir_str}")
            except ValueError:
                self.bot.reply_to(message, "❌ ID 必须是数字")

        @self.bot.message_handler(commands=["help"])
        def handle_help(message):
            self.bot.reply_to(
                message,
                "*📖 命令列表*\n\n"
                "/list — 查看所有监控项\n"
                "/stop N — 停止第 N 项\n"
                "/start N — 恢复第 N 项\n"
                "/rm N — 删除第 N 项\n"
                "/setdir N gt|lt — 设置费率报警方向\n"
                "/addprice 合约 下限 上限 — 添加价格监控\n"
                "/addfunding 合约 — 添加费率监控\n"
                "/funding — 查看当前数据\n"
                "/interval N — 设置轮询间隔(秒)\n"
                "/help — 本帮助",
                parse_mode="Markdown",
            )

    # ──────────────────────────────────────
    # Formatting
    # ──────────────────────────────────────
    def _format_status(self) -> str:
        now_str = datetime.now(CST).strftime("%H:%M:%S")
        lines = [f"📊 *监控状态* ({now_str} CST)", ""]

        # Funding rate monitors
        fr_monitors = [m for m in self.monitors if m.type == "funding_rate"]
        if fr_monitors:
            lines.append("*资金费率:*")
            lines.append("```")
            lines.append(f"{'合约':<10} {'Lighter':>10} {'TradeXYZ':>10} {'差值':>10}")
            lines.append("-" * 44)
            for m in fr_monitors:
                xyz_sym = SYMBOL_MAP.get(m.symbol, m.symbol)
                lig_rate_raw = self.latest_lighter_rates.get(m.symbol, {}).get("lighter")
                xyz_rate = self.latest_xyz_rates.get(xyz_sym, {}).get("funding")
                lig_rate = lig_rate_raw / LIGHTER_FUNDING_HOURS if lig_rate_raw is not None else None
                lig_s = f"{lig_rate*100:.4f}%" if lig_rate is not None else "N/A"
                xyz_s = f"{xyz_rate*100:.4f}%" if xyz_rate is not None else "N/A"
                if lig_rate is not None and xyz_rate is not None:
                    diff_s = f"{(lig_rate-xyz_rate)*100:+.4f}%"
                else:
                    diff_s = "N/A"
                status = "⏸" if not m.enabled else ""
                lines.append(f"{status}{m.symbol:<9} {lig_s:>10} {xyz_s:>10} {diff_s:>10}")
            lines.append("```")

        # Price range monitors
        pr_monitors = [m for m in self.monitors if m.type == "price_range"]
        if pr_monitors:
            lines.append("")
            lines.append("*价格范围:*")
            lines.append("```")
            lines.append(f"{'合约':<10} {'当前价':>10} {'区间':<16} {'状态':>6}")
            lines.append("-" * 46)
            for m in pr_monitors:
                price_data = self.latest_prices.get(m.symbol)
                px = price_data["mark"] if price_data else None
                px_s = f"{px:.4f}" if px is not None else "N/A"
                rng = f"{m.price_low}~{m.price_high}"
                if px is not None and m.price_low is not None:
                    in_range = m.price_low <= px <= m.price_high
                    st = "✅" if in_range else "⚠️"
                else:
                    st = "?"
                status = "⏸" if not m.enabled else ""
                lines.append(f"{status}{m.symbol:<9} {px_s:>10} {rng:<16} {st:>6}")
            lines.append("```")

        if not fr_monitors and not pr_monitors:
            lines.append("_暂无监控项，用 /addfunding 或 /addprice 添加_")

        return "\n".join(lines)

    # ──────────────────────────────────────
    # Alert senders
    # ──────────────────────────────────────
    def _send_funding_alert(self, m: MonitorItem, lig_rate: float, xyz_rate: float):
        if not self.bot or not TELEGRAM_CHAT_ID or not m.can_alert():
            return
        diff = lig_rate - xyz_rate
        xyz_sym = SYMBOL_MAP.get(m.symbol, m.symbol)
        now_str = datetime.now(CST).strftime("%H:%M:%S")
        msg = (
            f"🚨 *资金费率信号* #{m.id} ({now_str})\n\n"
            f"合约: *{m.symbol}*\n"
            f"Lighter: `{lig_rate*100:.4f}%`\n"
            f"TradeXYZ ({xyz_sym}): `{xyz_rate*100:.4f}%`\n"
            f"差值: `{diff*100:+.4f}%`"
        )
        try:
            self.bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="Markdown")
            m.mark_alerted()
            logger.info(f"Funding alert #{m.id} {m.symbol}: diff={diff*100:+.4f}%")
        except Exception as e:
            logger.error(f"Failed to send funding alert: {e}")

    def _send_price_alert(self, m: MonitorItem, price: float):
        if not self.bot or not TELEGRAM_CHAT_ID or not m.can_alert():
            return
        now_str = datetime.now(CST).strftime("%H:%M:%S")
        direction = "⬆️ 高于上限" if price > m.price_high else "⬇️ 低于下限"
        msg = (
            f"🔔 *价格越界* #{m.id} ({now_str})\n\n"
            f"合约: *{m.symbol}*\n"
            f"当前价: `{price:.4f}`\n"
            f"安全区间: `{m.price_low} ~ {m.price_high}`\n"
            f"状态: {direction}"
        )
        try:
            self.bot.send_message(TELEGRAM_CHAT_ID, msg, parse_mode="Markdown")
            m.mark_alerted()
            logger.info(f"Price alert #{m.id} {m.symbol}: {price:.4f} outside [{m.price_low}, {m.price_high}]")
        except Exception as e:
            logger.error(f"Failed to send price alert: {e}")

    # ──────────────────────────────────────
    # Data fetching & checking
    # ──────────────────────────────────────
    async def _fetch_all(self):
        """Fetch rates and prices concurrently."""
        has_funding = any(m.type == "funding_rate" and m.enabled for m in self.monitors)
        has_price = any(m.type == "price_range" and m.enabled for m in self.monitors)

        tasks = {}
        if has_funding:
            tasks["lig_rates"] = asyncio.create_task(self.lighter.get_funding_rates())
            tasks["xyz_rates"] = asyncio.create_task(self.hyperliquid.get_funding_rates())
        if has_price:
            tasks["prices"] = asyncio.create_task(self.lighter.get_market_prices())

        for name, task in tasks.items():
            try:
                result = await task
                if name == "lig_rates":
                    self.latest_lighter_rates = result
                elif name == "xyz_rates":
                    self.latest_xyz_rates = result
                elif name == "prices":
                    self.latest_prices = result
            except Exception as e:
                logger.error(f"Fetch error ({name}): {e}")

    async def _check_and_alert(self):
        """Check all monitors and send alerts."""
        await self._fetch_all()

        for m in self.monitors:
            if not m.enabled:
                continue

            if m.type == "funding_rate":
                xyz_sym = SYMBOL_MAP.get(m.symbol, m.symbol)
                lig_rate_raw = self.latest_lighter_rates.get(m.symbol, {}).get("lighter")
                xyz_rate = self.latest_xyz_rates.get(xyz_sym, {}).get("funding")
                lig_rate = lig_rate_raw / LIGHTER_FUNDING_HOURS if lig_rate_raw is not None else None

                if lig_rate is None or xyz_rate is None:
                    continue

                diff = lig_rate - xyz_rate
                logger.info(
                    f"#{m.id} {m.symbol}: Lighter={lig_rate*100:.4f}%  "
                    f"TradeXYZ({xyz_sym})={xyz_rate*100:.4f}%  "
                    f"diff={diff*100:+.4f}%"
                )

                should_alert = (
                    (m.alert_dir == "gt" and lig_rate > xyz_rate) or
                    (m.alert_dir == "lt" and lig_rate < xyz_rate)
                )
                if should_alert:
                    self._send_funding_alert(m, lig_rate, xyz_rate)

            elif m.type == "price_range":
                price_data = self.latest_prices.get(m.symbol)
                if not price_data:
                    continue
                px = price_data["mark"]
                in_range = m.price_low <= px <= m.price_high
                logger.info(
                    f"#{m.id} {m.symbol}: price={px:.4f}  "
                    f"range=[{m.price_low}, {m.price_high}]  "
                    f"{'✅' if in_range else '⚠️ OUT'}"
                )
                if not in_range:
                    self._send_price_alert(m, px)

    # ──────────────────────────────────────
    # Bot polling & main loop
    # ──────────────────────────────────────
    def _start_bot_polling(self):
        if not self.bot:
            return

        def run():
            try:
                if TELEGRAM_CHAT_ID:
                    lines = ["🚀 *监控系统已启动*", ""]
                    for m in self.monitors:
                        lines.append(m.describe())
                    lines.append(f"\n轮询间隔: {self.poll_interval}s")
                    lines.append("\n发 /help 查看命令")
                    self.bot.send_message(TELEGRAM_CHAT_ID, "\n".join(lines), parse_mode="Markdown")
            except Exception as e:
                logger.error(f"Startup msg failed: {e}")

            while self._running:
                try:
                    self.bot.polling(non_stop=True, interval=2, timeout=30)
                except Exception as e:
                    logger.error(f"Bot polling error: {e}")
                    time.sleep(5)

        threading.Thread(target=run, daemon=True).start()
        logger.info("Telegram bot polling started")

    async def start(self):
        logger.info(
            f"Starting Monitor | {len(self.monitors)} items | interval={self.poll_interval}s"
        )
        self._start_bot_polling()
        await self._check_and_alert()

        while self._running:
            await asyncio.sleep(self.poll_interval)
            try:
                await self._check_and_alert()
            except Exception as e:
                logger.error(f"Monitor loop error: {e}")
                await asyncio.sleep(10)


if __name__ == "__main__":
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add("logs/funding_rate_monitor.log", rotation="10 MB", retention="7 days", level="INFO")

    monitor = FundingRateMonitor()
    try:
        asyncio.run(monitor.start())
    except KeyboardInterrupt:
        monitor._running = False
        logger.info("Monitor stopped.")
