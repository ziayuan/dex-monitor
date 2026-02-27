"""Backpack-Variational Hedge Floating Window.

独立的 BP-Var 对冲浮窗应用。
"""
import asyncio
import json
import subprocess
import sys
import threading
import time
import tkinter as tk
from decimal import Decimal
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from core.config import load_config
from core.clicker import load_coordinates, perform_clicks, perform_var_click, reset_interrupt, set_interrupt_callback, get_coordinates, save_coordinates
from core import data_feeds
from exchanges.backpack import BackpackClient, BBO, OrderSide
from strategies.var_backpack import VarBackpackStrategy, HedgeDirection, HedgeState, StrategyConfig


# Configure loguru
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{message}</cyan>",
    level="INFO"
)
logger.add(
    "logs/bp_hedge_{time:YYYY-MM-DD}.log",
    rotation="1 day",
    retention="7 days",
    level="DEBUG"
)


# Load config
CFG = load_config()

# UI Theme
THEME = {
    "bg_window": "#1C1C1E",
    "bg_card": "#2C2C2E",
    "bg_input": "#3A3A3C",
    "fg_primary": "#FFFFFF",
    "fg_secondary": "#98989D",
    "accent": "#0A84FF",
    "green": "#32D74B",
    "red": "#FF453A",
    "yellow": "#FFD60A",
    "orange": "#FF9F0A",
}
FONT_TITLE = ("Helvetica Neue", 13, "bold")
FONT_BODY = ("Helvetica Neue", 12)
FONT_SMALL = ("Helvetica Neue", 10)
FONT_MONO = ("Monaco", 11)


class BPHedgeWindow:
    """Backpack-Variational Hedge Floating Window."""
    
    def __init__(self):
        # Load coordinates
        self.coords_loaded = load_coordinates()
        set_interrupt_callback(self.emergency_stop)
        
        # Initialize Backpack client
        try:
            self.bp_client = BackpackClient()
        except Exception as e:
            logger.error(f"Failed to init BP client: {e}")
            self.bp_client = None
        
        # Initialize data feeds for Var prices
        data_feeds.init(CFG)
        data_feeds.set_price_callback(self._on_var_price_update)
        
        # Get BP pairs from config
        self.bp_pairs = CFG.get("backpack", {}).get("pairs", [
            {"symbol": "BTC_USDC_PERP", "var_symbol": "BTC-USD", "tick_size": "0.1"},
            {"symbol": "ETH_USDC_PERP", "var_symbol": "ETH-USD", "tick_size": "0.01"},
        ])
        self.current_pair_idx = 0
        
        # Strategy
        self.strategy = VarBackpackStrategy(
            bp_client=self.bp_client,
            click_callback=perform_var_click,
        )
        self.strategy.set_callbacks(
            on_state_change=self._on_state_change,
            on_trade=self._on_trade
        )
        
        # Async state
        self.bp_tasks = []
        
        # Prices
        self.bp_bbo = BBO()
        self.var_bbo = {"bid": Decimal(0), "ask": Decimal(0)}
        
        # Create window
        self.root = tk.Tk()
        self.root.title("BP-Var Hedge")
        self.root.attributes("-topmost", True)
        # self.root.overrideredirect(True)  # DISABLED - causes keyboard focus issues on macOS
        self.root.attributes("-alpha", 0.95)
        self.root.configure(bg=THEME["bg_window"])
        self.root.geometry("420x400+100+100")
        
        self._create_ui()
        
        # Async loop
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.thread.start()
        
        # UI update loop
        self.root.after(200, self._update_ui)
    
    def _create_ui(self):
        """Create the UI components."""
        # Header with traffic lights
        self._create_header()
        
        # Price display
        self._create_price_panel()
        
        # Strategy config
        self._create_strategy_panel()
        
        # Status display
        self._create_status_panel()
        
        # Drag bindings
        for w in [self.root, self.header]:
            w.bind("<Button-1>", self._start_move)
            w.bind("<B1-Motion>", self._do_move)
    
    def _create_header(self):
        """Create header with traffic lights and pair selector."""
        self.header = tk.Frame(self.root, bg=THEME["bg_window"], height=30)
        self.header.pack(fill=tk.X, padx=10, pady=(8, 5))
        
        # Traffic lights
        f = tk.Frame(self.header, bg=THEME["bg_window"])
        f.pack(side=tk.LEFT)
        for color, cmd in [(THEME["red"], self.root.quit), (THEME["yellow"], None), (THEME["green"], None)]:
            c = tk.Canvas(f, width=12, height=12, bg=THEME["bg_window"], highlightthickness=0)
            c.create_oval(1, 1, 11, 11, fill=color, outline="")
            c.pack(side=tk.LEFT, padx=3)
            if cmd:
                c.bind("<Button-1>", lambda e, c=cmd: c())
        
        # Title
        tk.Label(self.header, text="BP-Var Hedge", bg=THEME["bg_window"],
                 fg=THEME["fg_secondary"], font=FONT_SMALL).pack(side=tk.LEFT, padx=10)
        
        # Pair selector
        pair = self.bp_pairs[self.current_pair_idx]
        self.pair_btn = tk.Label(self.header, text=pair["symbol"],
                                  bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                  font=FONT_SMALL, padx=8, pady=2, cursor="hand2")
        self.pair_btn.pack(side=tk.RIGHT)
        self.pair_btn.bind("<Button-1>", self._cycle_pair)
    
    def _create_price_panel(self):
        """Create price display panel."""
        self.price_frame = tk.Frame(self.root, bg=THEME["bg_card"], pady=10, padx=10)
        self.price_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # BP prices
        r1 = tk.Frame(self.price_frame, bg=THEME["bg_card"])
        r1.pack(fill=tk.X, pady=2)
        
        tk.Label(r1, text="Backpack", bg=THEME["bg_card"], fg=THEME["accent"],
                 font=FONT_BODY, width=10, anchor="w").pack(side=tk.LEFT)
        self.l_bp_bid = tk.Label(r1, text="Bid: --", bg=THEME["bg_card"],
                                  fg=THEME["green"], font=FONT_MONO)
        self.l_bp_bid.pack(side=tk.LEFT, padx=5)
        self.l_bp_ask = tk.Label(r1, text="Ask: --", bg=THEME["bg_card"],
                                  fg=THEME["red"], font=FONT_MONO)
        self.l_bp_ask.pack(side=tk.LEFT, padx=5)
        
        # Var prices
        r2 = tk.Frame(self.price_frame, bg=THEME["bg_card"])
        r2.pack(fill=tk.X, pady=2)
        
        tk.Label(r2, text="Variational", bg=THEME["bg_card"], fg=THEME["orange"],
                 font=FONT_BODY, width=10, anchor="w").pack(side=tk.LEFT)
        self.l_var_bid = tk.Label(r2, text="Bid: --", bg=THEME["bg_card"],
                                   fg=THEME["green"], font=FONT_MONO)
        self.l_var_bid.pack(side=tk.LEFT, padx=5)
        self.l_var_ask = tk.Label(r2, text="Ask: --", bg=THEME["bg_card"],
                                   fg=THEME["red"], font=FONT_MONO)
        self.l_var_ask.pack(side=tk.LEFT, padx=5)
        
        # Spread display
        r3 = tk.Frame(self.price_frame, bg=THEME["bg_card"])
        r3.pack(fill=tk.X, pady=(8, 0))
        
        s1 = tk.Frame(r3, bg=THEME["bg_card"])
        s1.pack(side=tk.LEFT, expand=True)
        tk.Label(s1, text="Var卖 (BP买)", bg=THEME["bg_card"],
                 fg=THEME["fg_secondary"], font=FONT_SMALL).pack()
        self.l_spread_vs = tk.Label(s1, text="--", bg=THEME["bg_card"],
                                     fg=THEME["fg_primary"], font=FONT_TITLE)
        self.l_spread_vs.pack()
        
        s2 = tk.Frame(r3, bg=THEME["bg_card"])
        s2.pack(side=tk.LEFT, expand=True)
        tk.Label(s2, text="BP卖 (Var买)", bg=THEME["bg_card"],
                 fg=THEME["fg_secondary"], font=FONT_SMALL).pack()
        self.l_spread_bs = tk.Label(s2, text="--", bg=THEME["bg_card"],
                                     fg=THEME["fg_primary"], font=FONT_TITLE)
        self.l_spread_bs.pack()
    
    def _create_strategy_panel(self):
        """Create strategy configuration panel."""
        self.strat_frame = tk.Frame(self.root, bg=THEME["bg_card"], pady=8, padx=10)
        self.strat_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Row 1: Direction selection
        r1 = tk.Frame(self.strat_frame, bg=THEME["bg_card"])
        r1.pack(fill=tk.X, pady=2)
        
        tk.Label(r1, text="方向", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL, width=6, anchor="w").pack(side=tk.LEFT)
        
        self.dir_var = tk.StringVar(value="var_sell")
        
        self.rb_vs = tk.Radiobutton(r1, text="Var卖", variable=self.dir_var, value="var_sell",
                                     bg=THEME["bg_card"], fg=THEME["fg_primary"],
                                     selectcolor=THEME["bg_input"], font=FONT_SMALL,
                                     activebackground=THEME["bg_card"])
        self.rb_vs.pack(side=tk.LEFT, padx=5)
        
        self.rb_bs = tk.Radiobutton(r1, text="BP卖", variable=self.dir_var, value="bp_sell",
                                     bg=THEME["bg_card"], fg=THEME["fg_primary"],
                                     selectcolor=THEME["bg_input"], font=FONT_SMALL,
                                     activebackground=THEME["bg_card"])
        self.rb_bs.pack(side=tk.LEFT, padx=5)
        
        # Row 2: Threshold
        r2 = tk.Frame(self.strat_frame, bg=THEME["bg_card"])
        r2.pack(fill=tk.X, pady=2)
        
        tk.Label(r2, text="阈值 >=", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT)
        self.ent_thresh = tk.Entry(r2, width=6, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                    bd=0, font=FONT_MONO, justify="center")
        self.ent_thresh.insert(0, "5.0")
        self.ent_thresh.pack(side=tk.LEFT, padx=5)
        
        tk.Label(r2, text="数量", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(15, 0))
        self.ent_qty = tk.Entry(r2, width=8, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                 bd=0, font=FONT_MONO, justify="center")
        self.ent_qty.insert(0, "0.01")
        self.ent_qty.pack(side=tk.LEFT, padx=5)
        
        # Row 3: Max trades and cooldown
        r3 = tk.Frame(self.strat_frame, bg=THEME["bg_card"])
        r3.pack(fill=tk.X, pady=2)
        
        tk.Label(r3, text="Max", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT)
        self.ent_max = tk.Entry(r3, width=4, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                 bd=0, font=FONT_MONO, justify="center")
        self.ent_max.insert(0, "10")
        self.ent_max.pack(side=tk.LEFT, padx=5)
        
        tk.Label(r3, text="Cd(s)", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(10, 0))
        self.ent_cd = tk.Entry(r3, width=4, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                bd=0, font=FONT_MONO, justify="center")
        self.ent_cd.insert(0, "5")
        self.ent_cd.pack(side=tk.LEFT, padx=5)
        
        # Start/Stop button
        self.btn_toggle = tk.Label(r3, text="START", bg=THEME["green"], fg="white",
                                    font=FONT_BODY, width=10, pady=4, cursor="hand2")
        self.btn_toggle.pack(side=tk.RIGHT)
        self.btn_toggle.bind("<Button-1>", lambda e: self._toggle_strategy())
    
    def _create_status_panel(self):
        """Create status display panel."""
        self.status_frame = tk.Frame(self.root, bg=THEME["bg_card"], pady=8, padx=10)
        self.status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # State indicator
        r1 = tk.Frame(self.status_frame, bg=THEME["bg_card"])
        r1.pack(fill=tk.X, pady=2)
        
        tk.Label(r1, text="状态", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL, width=6, anchor="w").pack(side=tk.LEFT)
        self.l_state = tk.Label(r1, text="IDLE", bg=THEME["bg_card"],
                                 fg=THEME["fg_primary"], font=FONT_BODY)
        self.l_state.pack(side=tk.LEFT, padx=5)
        
        self.l_order = tk.Label(r1, text="", bg=THEME["bg_card"],
                                 fg=THEME["fg_secondary"], font=FONT_SMALL)
        self.l_order.pack(side=tk.LEFT, padx=10)
        
        # Trade counter and PnL
        r2 = tk.Frame(self.status_frame, bg=THEME["bg_card"])
        r2.pack(fill=tk.X, pady=2)
        
        tk.Label(r2, text="交易", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL, width=6, anchor="w").pack(side=tk.LEFT)
        self.l_trades = tk.Label(r2, text="0/10", bg=THEME["bg_card"],
                                  fg=THEME["fg_primary"], font=FONT_MONO)
        self.l_trades.pack(side=tk.LEFT, padx=5)
        
        tk.Label(r2, text="PnL", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(15, 0))
        self.l_pnl = tk.Label(r2, text="+0.00", bg=THEME["bg_card"],
                               fg=THEME["green"], font=FONT_MONO)
        self.l_pnl.pack(side=tk.LEFT, padx=5)
        
        # Coords status
        r3 = tk.Frame(self.status_frame, bg=THEME["bg_card"])
        r3.pack(fill=tk.X, pady=(5, 0))
        
        coords_status = "✅ 坐标已加载" if self.coords_loaded else "❌ 无坐标"
        coords_color = THEME["green"] if self.coords_loaded else THEME["red"]
        self.l_coords = tk.Label(r3, text=coords_status, bg=THEME["bg_card"],
                                  fg=coords_color, font=FONT_SMALL)
        self.l_coords.pack(side=tk.LEFT)
        
        bp_status = "✅ BP 已连接" if self.bp_client else "❌ BP 未连接"
        bp_color = THEME["green"] if self.bp_client else THEME["red"]
        self.l_bp_status = tk.Label(r3, text=bp_status, bg=THEME["bg_card"],
                                     fg=bp_color, font=FONT_SMALL)
        self.l_bp_status.pack(side=tk.RIGHT)
        
        # Coordinate update row
        r4 = tk.Frame(self.status_frame, bg=THEME["bg_card"])
        r4.pack(fill=tk.X, pady=(5, 0))
        
        var_pos, _ = get_coordinates()
        coord_text = f"Var: ({var_pos.x}, {var_pos.y})" if var_pos else "Var: 未设置"
        self.l_var_coord = tk.Label(r4, text=coord_text, bg=THEME["bg_card"],
                                     fg=THEME["fg_secondary"], font=FONT_SMALL)
        self.l_var_coord.pack(side=tk.LEFT)
        
        self.btn_record_var = tk.Label(r4, text="更新Var坐标", bg=THEME["accent"],
                                        fg="white", font=FONT_SMALL, padx=6, pady=2,
                                        cursor="hand2")
        self.btn_record_var.pack(side=tk.RIGHT)
        self.btn_record_var.bind("<Button-1>", lambda e: self._start_record_var())
    
    # ========== Event Handlers ==========
    
    def _cycle_pair(self, event):
        """Cycle through trading pairs."""
        self.current_pair_idx = (self.current_pair_idx + 1) % len(self.bp_pairs)
        pair = self.bp_pairs[self.current_pair_idx]
        self.pair_btn.config(text=pair["symbol"])
        
        # Update Var data feed
        data_feeds.set_current_symbol(pair.get("var_symbol", "BTC-USD"))
        
        # Reset prices
        self.bp_bbo = BBO()
        self.var_bbo = {"bid": Decimal(0), "ask": Decimal(0)}
        
        logger.info(f"Switched to {pair['symbol']}")
        
        # Trigger async subscription update
        if self.bp_client:
            asyncio.run_coroutine_threadsafe(
                self._update_bp_subscription(pair["symbol"]),
                self.loop
            )
    
    def _start_move(self, event):
        self.x, self.y = event.x, event.y
    
    def _do_move(self, event):
        x = self.root.winfo_x() + (event.x - self.x)
        y = self.root.winfo_y() + (event.y - self.y)
        self.root.geometry(f"+{x}+{y}")
    
    def _start_record_var(self):
        """Start 3-second countdown to record Var position."""
        import pyautogui
        self._record_countdown = 3
        self.btn_record_var.config(text=f"3秒后记录...", bg=THEME["yellow"])
        self._countdown_tick()
    
    def _countdown_tick(self):
        """Handle countdown tick."""
        import pyautogui
        if self._record_countdown > 0:
            self.btn_record_var.config(text=f"{self._record_countdown}秒后记录...")
            self._record_countdown -= 1
            self.root.after(1000, self._countdown_tick)
        else:
            # Record position
            pos = pyautogui.position()
            _, lig_pos = get_coordinates()
            lig_tuple = (lig_pos.x, lig_pos.y) if lig_pos else (0, 0)
            save_coordinates((pos.x, pos.y), lig_tuple)
            load_coordinates()  # Reload to update module state
            
            # Update UI
            self.l_var_coord.config(text=f"Var: ({pos.x}, {pos.y})")
            self.btn_record_var.config(text="更新Var坐标", bg=THEME["accent"])
            self.l_coords.config(text="✅ 坐标已更新", fg=THEME["green"])
            logger.info(f"Var坐标已更新: ({pos.x}, {pos.y})")
    
    def _toggle_strategy(self):
        """Start or stop strategy."""
        if not self.strategy.running:
            # Validate
            if not self.coords_loaded:
                self.coords_loaded = load_coordinates()
            if not self.coords_loaded:
                logger.error("No coordinates loaded!")
                return
            if not self.bp_client:
                logger.error("BP client not connected!")
                return
            
            # Get config
            try:
                threshold = Decimal(self.ent_thresh.get())
                quantity = Decimal(self.ent_qty.get())
                max_trades = int(self.ent_max.get())
                cooldown = float(self.ent_cd.get())
            except Exception as e:
                logger.error(f"Invalid config: {e}")
                return
            
            direction = HedgeDirection.VAR_SELL if self.dir_var.get() == "var_sell" else HedgeDirection.BP_SELL
            
            # Get tick size from pair config
            pair = self.bp_pairs[self.current_pair_idx]
            tick_size = Decimal(pair.get("tick_size", "0.1"))
            
            # Configure and start
            self.strategy.configure(
                direction=direction,
                threshold=threshold,
                quantity=quantity,
                max_trades=max_trades,
                cooldown=cooldown
            )
            self.strategy.config.tick_size = tick_size
            
            reset_interrupt()
            self.strategy.start(pair["symbol"])
            
            self.btn_toggle.config(text="STOP", bg=THEME["red"])
            self._update_trades_display()
        else:
            self.strategy.stop()
            self.btn_toggle.config(text="START", bg=THEME["green"])
    
    def emergency_stop(self):
        """Emergency stop on mouse movement."""
        if self.strategy.running:
            self.strategy.stop()
            self.root.after(0, lambda: self.btn_toggle.config(text="START", bg=THEME["green"]))
            logger.warning("⚠️ Emergency stop triggered!")
    
    # ========== Callbacks ==========
    
    def _on_bp_bbo_update(self, symbol: str, bbo: BBO):
        """Callback for BP BBO updates."""
        self.bp_bbo = bbo
        self.strategy.update_bp_prices(bbo.bid, bbo.ask)
    
    def _on_var_price_update(self):
        """Callback for Var price updates from data_feeds."""
        prices = data_feeds.get_prices()
        if prices:
            self.var_bbo["bid"] = Decimal(str(prices.get("var", {}).get("bid", 0)))
            self.var_bbo["ask"] = Decimal(str(prices.get("var", {}).get("ask", 0)))
            self.strategy.update_var_prices(self.var_bbo["bid"], self.var_bbo["ask"])
    
    def _on_bp_order_update(self, order_data: dict):
        """Callback for BP order updates."""
        asyncio.run_coroutine_threadsafe(
            self.strategy.on_bp_order_update(order_data),
            self.loop
        )
    
    def _on_state_change(self, new_state: HedgeState):
        """Callback for strategy state changes."""
        self.root.after(0, lambda: self._update_state_display(new_state))
    
    def _on_trade(self, trade_data: dict):
        """Callback for completed trades."""
        self.root.after(0, self._update_trades_display)
        
        # Play sound
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        
        # Check if max trades reached
        if self.strategy.state.trades_executed >= self.strategy.config.max_trades:
            self.strategy.stop()
            self.root.after(0, lambda: self.btn_toggle.config(text="START", bg=THEME["green"]))
            logger.info("🔔 已达到最大交易次数，自动停止")
    
    # ========== UI Updates ==========
    
    def _update_ui(self):
        """Periodic UI update."""
        # Update BP prices
        if self.bp_bbo.bid:
            self.l_bp_bid.config(text=f"Bid: {self.bp_bbo.bid:.2f}")
            self.l_bp_ask.config(text=f"Ask: {self.bp_bbo.ask:.2f}")
        
        # Update Var prices
        if self.var_bbo["bid"]:
            self.l_var_bid.config(text=f"Bid: {self.var_bbo['bid']:.2f}")
            self.l_var_ask.config(text=f"Ask: {self.var_bbo['ask']:.2f}")
        
        # Update spreads
        # Var RFQ模式: bid是做市商买入价(你卖出), ask是做市商卖出价(你买入)
        # Var的bid > ask (做市商给的价差)
        
        # Var卖方向: 在BP挂买单 -> 成交 -> 在Var点击卖出
        # 价差 = BP_bid (你卖给BP的价格) - Var_bid (你卖给Var的价格) 
        # 注: 这里应该是 Var_bid - BP_bid，因为你在Var卖出获得更高价格
        if self.bp_bbo.bid and self.var_bbo["bid"]:
            spread_vs = self.var_bbo["bid"] - self.bp_bbo.bid
            color = THEME["green"] if spread_vs > 0 else THEME["red"]
            self.l_spread_vs.config(text=f"{spread_vs:+.2f}", fg=color)
        
        # BP卖方向: 在BP挂卖单 -> 成交 -> 在Var点击买入
        # 价差 = BP_ask (你从BP买入的价格) - Var_ask (你从Var买入的价格)
        # 注: 这里应该是 BP_ask - Var_ask，因为你在BP卖出获得更高价格
        if self.var_bbo["ask"] and self.bp_bbo.ask:
            spread_bs = self.bp_bbo.ask - self.var_bbo["ask"]
            color = THEME["green"] if spread_bs > 0 else THEME["red"]
            self.l_spread_bs.config(text=f"{spread_bs:+.2f}", fg=color)
        
        # Keep on top
        self.root.lift()
        self.root.attributes("-topmost", True)
        
        self.root.after(200, self._update_ui)
    
    def _update_state_display(self, state: HedgeState):
        """Update state label."""
        state_text = {
            HedgeState.IDLE: "🟢 IDLE",
            HedgeState.PLACING: "🟡 PLACING",
            HedgeState.PENDING: "🟠 PENDING",
            HedgeState.HEDGING: "🔴 HEDGING",
            HedgeState.COOLDOWN: "⏳ COOLDOWN"
        }
        self.l_state.config(text=state_text.get(state, "IDLE"))
        
        if state == HedgeState.PENDING and self.strategy.state.pending_order_id:
            self.l_order.config(text=f"Order: {self.strategy.state.pending_order_id[:8]}...")
        else:
            self.l_order.config(text="")
    
    def _update_trades_display(self):
        """Update trades counter and PnL."""
        trades = self.strategy.state.trades_executed
        max_t = self.strategy.config.max_trades
        self.l_trades.config(text=f"{trades}/{max_t}")
        
        pnl = self.strategy.state.total_pnl
        color = THEME["green"] if pnl >= 0 else THEME["red"]
        self.l_pnl.config(text=f"{pnl:+.4f}", fg=color)
    
    # ========== Async Loop ==========
    
    def _run_async_loop(self):
        """Run async event loop in background thread."""
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main_task())
    
    async def _main_task(self):
        """Main async task - connect WebSockets."""
        tasks = [data_feeds.monitor_variational()]
        tasks.append(data_feeds.monitor_paradex())
        
        # Initial BP subscription
        if self.bp_client:
            pair = self.bp_pairs[self.current_pair_idx]
            await self._update_bp_subscription(pair["symbol"])
        
        # Monitor data feeds (these run forever)
        await asyncio.gather(*tasks)

    async def _update_bp_subscription(self, symbol: str):
        """Update Backpack WebSocket subscription."""
        # Cancel existing tasks
        if self.bp_tasks:
            logger.info("Cancelling previous BP tasks...")
            for task in self.bp_tasks:
                task.cancel()
            
            # Wait for them to cancel to allow clean socket closure
            await asyncio.gather(*self.bp_tasks, return_exceptions=True)
            self.bp_tasks.clear()
            
        # Create new tasks
        if self.bp_client:
            logger.info(f"Starting BP subscription for {symbol}")
            t1 = asyncio.create_task(self.bp_client.subscribe_bbo(symbol, self._on_bp_bbo_update))
            t2 = asyncio.create_task(self.bp_client.subscribe_order_updates(symbol, self._on_bp_order_update))
            self.bp_tasks = [t1, t2]


if __name__ == "__main__":
    app = BPHedgeWindow()
    app.root.mainloop()
