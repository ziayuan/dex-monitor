import asyncio
import sys
import threading
import time
import tkinter as tk
from decimal import Decimal
from pathlib import Path
import subprocess

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger

from core.config import load_config
from core.clicker import load_coordinates, perform_clicks, set_interrupt_callback, get_coordinates, save_coordinates, reset_interrupt
from core import data_feeds
from exchanges.lighter import LighterClient
from strategies.var_lighter import VarLighterStrategy, HedgeDirection, StrategyConfig

# Configure loguru
logger.remove()
logger.add(
    sys.stderr,
    format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | <cyan>{message}</cyan>",
    level="INFO"
)
logger.add(
    "logs/lig_hedge_{time:YYYY-MM-DD}.log",
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
    "accent": "#5E5CE6",  # Indigo for Lighter
    "green": "#32D74B",
    "red": "#FF453A",
    "yellow": "#FFD60A",
    "orange": "#FF9F0A",
}
FONT_TITLE = ("Helvetica Neue", 13, "bold")
FONT_BODY = ("Helvetica Neue", 12)
FONT_SMALL = ("Helvetica Neue", 10)
FONT_MONO = ("Monaco", 11)


class LigHedgeWindow:
    """Lighter-Variational Hedge Floating Window."""
    
    def __init__(self):
        # Load coordinates
        self.coords_loaded = load_coordinates()
        set_interrupt_callback(self.emergency_stop)
        
        # Initialize Lighter client
        self.lig_client = None

        
        # Initialize data feeds for Var prices
        data_feeds.init(CFG)
        data_feeds.set_price_callback(self._on_var_price_update)
        
        # Get Lighter pairs from config
        self.lig_pairs = CFG.get("lighter", {}).get("pairs", [
            {"symbol": "BTC-USD", "var_symbol": "BTC-USD", "lig_symbol": "BTC"},
            {"symbol": "ETH-USD", "var_symbol": "ETH-USD", "lig_symbol": "ETH"},
        ])
        self.current_pair_idx = 0
        
        # Click guard flag – prevents drag events during automated clicks
        self._clicking = False
        
        # Strategy
        self.strategy = VarLighterStrategy(
            click_callback=self._safe_click,
        )
        self.strategy.set_callbacks(
            on_trade=self._on_trade
        )
        
        # Async state
        self.lig_monitor = None
        self.lig_task = None
        self._sub_serial = 0  # incremented each pair switch to cancel stale coroutines
        
        # Prices
        self.lig_bbo = {"bid": Decimal(0), "ask": Decimal(0)}
        self.var_bbo = {"bid": Decimal(0), "ask": Decimal(0)}
        
        # Create window
        self.root = tk.Tk()
        self.root.title("Lig-Var Hedge")
        self.root.attributes("-topmost", True)
        # self.root.overrideredirect(True)
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
        self._create_header()
        self._create_price_panel()
        self._create_strategy_panel()
        self._create_status_panel()
        
        for w in [self.root, self.header]:
            w.bind("<Button-1>", self._start_move)
            w.bind("<B1-Motion>", self._do_move)
    
    def _create_header(self):
        """Create header."""
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
        tk.Label(self.header, text="Lig-Var Hedge", bg=THEME["bg_window"],
                 fg=THEME["fg_secondary"], font=FONT_SMALL).pack(side=tk.LEFT, padx=10)
        
        # Pair selector
        pair = self.lig_pairs[self.current_pair_idx]
        self.pair_btn = tk.Label(self.header, text=pair["symbol"],
                                  bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                  font=FONT_SMALL, padx=8, pady=2, cursor="hand2")
        self.pair_btn.pack(side=tk.RIGHT)
        self.pair_btn.bind("<Button-1>", self._cycle_pair)
    
    def _create_price_panel(self):
        """Create price display panel."""
        self.price_frame = tk.Frame(self.root, bg=THEME["bg_card"], pady=10, padx=10)
        self.price_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Lig prices
        r1 = tk.Frame(self.price_frame, bg=THEME["bg_card"])
        r1.pack(fill=tk.X, pady=2)
        
        tk.Label(r1, text="Lighter", bg=THEME["bg_card"], fg=THEME["accent"],
                 font=FONT_BODY, width=10, anchor="w").pack(side=tk.LEFT)
        self.l_lig_bid = tk.Label(r1, text="Bid: --", bg=THEME["bg_card"],
                                   fg=THEME["green"], font=FONT_MONO)
        self.l_lig_bid.pack(side=tk.LEFT, padx=5)
        self.l_lig_ask = tk.Label(r1, text="Ask: --", bg=THEME["bg_card"],
                                   fg=THEME["red"], font=FONT_MONO)
        self.l_lig_ask.pack(side=tk.LEFT, padx=5)
        
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
        
        # Spread
        r3 = tk.Frame(self.price_frame, bg=THEME["bg_card"])
        r3.pack(fill=tk.X, pady=(8, 0))
        
        s1 = tk.Frame(r3, bg=THEME["bg_card"])
        s1.pack(side=tk.LEFT, expand=True)
        tk.Label(s1, text="Var卖 (Lig买)", bg=THEME["bg_card"],
                 fg=THEME["fg_secondary"], font=FONT_SMALL).pack()
        self.l_spread_vs = tk.Label(s1, text="--", bg=THEME["bg_card"],
                                     fg=THEME["fg_primary"], font=FONT_TITLE)
        self.l_spread_vs.pack()
        
        s2 = tk.Frame(r3, bg=THEME["bg_card"])
        s2.pack(side=tk.LEFT, expand=True)
        tk.Label(s2, text="Lig卖 (Var买)", bg=THEME["bg_card"],
                 fg=THEME["fg_secondary"], font=FONT_SMALL).pack()
        self.l_spread_ls = tk.Label(s2, text="--", bg=THEME["bg_card"],
                                     fg=THEME["fg_primary"], font=FONT_TITLE)
        self.l_spread_ls.pack()
    
    def _create_strategy_panel(self):
        """Create strategy config panel."""
        self.strat_frame = tk.Frame(self.root, bg=THEME["bg_card"], pady=8, padx=10)
        self.strat_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Row 1: Direction
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
        
        self.rb_ls = tk.Radiobutton(r1, text="Lig卖", variable=self.dir_var, value="lig_sell",
                                     bg=THEME["bg_card"], fg=THEME["fg_primary"],
                                     selectcolor=THEME["bg_input"], font=FONT_SMALL,
                                     activebackground=THEME["bg_card"])
        self.rb_ls.pack(side=tk.LEFT, padx=5)
        
        # Row 2: Threshold & Cooldown
        r2 = tk.Frame(self.strat_frame, bg=THEME["bg_card"])
        r2.pack(fill=tk.X, pady=2)
        
        tk.Label(r2, text="阈值 >=", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT)
        self.ent_thresh = tk.Entry(r2, width=6, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                    bd=0, font=FONT_MONO, justify="center")
        self.ent_thresh.insert(0, "5.0")
        self.ent_thresh.pack(side=tk.LEFT, padx=5)
        
        tk.Label(r2, text="Cd(s)", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(5, 0))
        self.ent_cd = tk.Entry(r2, width=4, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                bd=0, font=FONT_MONO, justify="center")
        self.ent_cd.insert(0, "3.0")
        self.ent_cd.pack(side=tk.LEFT, padx=5)

        tk.Label(r2, text="Max", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(5, 0))
        self.ent_max = tk.Entry(r2, width=4, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                 bd=0, font=FONT_MONO, justify="center")
        self.ent_max.insert(0, "10")
        self.ent_max.pack(side=tk.LEFT, padx=5)
        
        # Start button
        self.btn_toggle = tk.Label(r2, text="START", bg=THEME["green"], fg="white",
                                    font=FONT_BODY, width=8, pady=2, cursor="hand2")
        self.btn_toggle.pack(side=tk.RIGHT)
        self.btn_toggle.bind("<Button-1>", lambda e: self._toggle_strategy())
    
    def _create_status_panel(self):
        """Create status panel."""
        self.status_frame = tk.Frame(self.root, bg=THEME["bg_card"], pady=8, padx=10)
        self.status_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Trades
        r1 = tk.Frame(self.status_frame, bg=THEME["bg_card"])
        r1.pack(fill=tk.X, pady=2)
        
        tk.Label(r1, text="交易", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL, width=6, anchor="w").pack(side=tk.LEFT)
        self.l_trades = tk.Label(r1, text="0/10", bg=THEME["bg_card"],
                                  fg=THEME["fg_primary"], font=FONT_MONO)
        self.l_trades.pack(side=tk.LEFT, padx=5)
        
        # Connections
        r3 = tk.Frame(self.status_frame, bg=THEME["bg_card"])
        r3.pack(fill=tk.X, pady=(5, 0))
        
        coords_status = "✅ 坐标" if self.coords_loaded else "❌ 无坐标"
        coords_color = THEME["green"] if self.coords_loaded else THEME["red"]
        self.l_coords = tk.Label(r3, text=coords_status, bg=THEME["bg_card"],
                                  fg=coords_color, font=FONT_SMALL)
        self.l_coords.pack(side=tk.LEFT)
        
        lig_status = "✅ Lig" if self.lig_client else "❌ Lig Err"
        lig_color = THEME["green"] if self.lig_client else THEME["red"]
        self.l_lig_status = tk.Label(r3, text=lig_status, bg=THEME["bg_card"],
                                      fg=lig_color, font=FONT_SMALL)
        self.l_lig_status.pack(side=tk.RIGHT)
        
        # Coordinate update row
        r4 = tk.Frame(self.status_frame, bg=THEME["bg_card"])
        r4.pack(fill=tk.X, pady=(5, 0))
        
        var_pos, lig_pos = get_coordinates()
        
        self.l_coord_status = tk.Label(r4, text=f"V:{var_pos.x},{var_pos.y} L:{lig_pos.x},{lig_pos.y}" if var_pos and lig_pos else "--", 
                                       bg=THEME["bg_card"], fg=THEME["fg_secondary"], font=FONT_SMALL)
        self.l_coord_status.pack(side=tk.LEFT)
        
        self.btn_upd_coords = tk.Label(r4, text="更新坐标", bg=THEME["accent"],
                                         fg="white", font=FONT_SMALL, padx=6, pady=2, cursor="hand2")
        self.btn_upd_coords.pack(side=tk.RIGHT)
        self.btn_upd_coords.bind("<Button-1>", lambda e: self._start_coord_update())

    # ========== Event Handlers ==========
    
    def _cycle_pair(self, event):
        self.current_pair_idx = (self.current_pair_idx + 1) % len(self.lig_pairs)
        pair = self.lig_pairs[self.current_pair_idx]
        self.pair_btn.config(text=pair["symbol"])
        
        # Reset prices
        self.lig_bbo = {"bid": Decimal(0), "ask": Decimal(0)}
        self.var_bbo = {"bid": Decimal(0), "ask": Decimal(0)}
        
        # Update Var symbol
        data_feeds.set_current_symbol(pair["var_symbol"])
        
        # Trigger async subscription update
        asyncio.run_coroutine_threadsafe(
            self._update_lig_subscription(pair["lig_symbol"]),
            self.loop
        )
        logger.info(f"Switched to {pair['symbol']}")
    
    def _start_move(self, event):
        if self._clicking:
            return
        self.x, self.y = event.x, event.y
    
    def _do_move(self, event):
        if self._clicking:
            return
        x = self.root.winfo_x() + (event.x - self.x)
        y = self.root.winfo_y() + (event.y - self.y)
        self.root.geometry(f"+{x}+{y}")
    
    def _safe_click(self):
        """Execute clicks with drag protection."""
        self._clicking = True
        try:
            perform_clicks(stabilization_ms=20)
        finally:
            self._clicking = False
        
    def _toggle_strategy(self):
        if not self.strategy.running:
            # Validate
            if not self.coords_loaded:
                self.coords_loaded = load_coordinates()
            if not self.coords_loaded:
                logger.error("No coordinates loaded!")
                return
                
            try:
                threshold = Decimal(self.ent_thresh.get())
                cooldown = float(self.ent_cd.get())
                max_trades = int(self.ent_max.get())
            except Exception:
                return
            
            direction = HedgeDirection.VAR_SELL if self.dir_var.get() == "var_sell" else HedgeDirection.LIG_SELL
            
            self.strategy.configure(direction, threshold, cooldown, max_trades)
            self.strategy.reset()  # Reset state (trades count, etc)
            reset_interrupt()
            
            pair = self.lig_pairs[self.current_pair_idx]
            self.strategy.start(pair["symbol"])
            
            self.btn_toggle.config(text="STOP", bg=THEME["red"])
            self.l_trades.config(text=f"0/{max_trades}")
        else:
            self.strategy.stop()
            self.btn_toggle.config(text="START", bg=THEME["green"])

    def _start_coord_update(self):
        """Update coordinates flow."""
        # This is a bit complex as we need to update two points
        # For now, let's just trigger the same flow or maybe separate buttons?
        # Let's add simple one-by-one update if needed, or rely on external tool
        # Re-using the logic from bp_hedge_window but for dual points might be tricky in simple UI
        # We'll just launch a separate prompt or update Var only? 
        # Requirement said "Update Var click coordinates", but we need both for Lighter.
        # Let's implement a simple 2-step wizard.
        self._coord_step = 0
        self.btn_upd_coords.config(text="移至Var...", bg=THEME["yellow"])
        self.root.after(3000, self._record_var_coord)
    
    def _record_var_coord(self):
        import pyautogui
        pos = pyautogui.position()
        self._temp_var = pos
        logger.info(f"Recorded Var: {pos}")
        self.btn_upd_coords.config(text="移至Lig...", bg=THEME["yellow"])
        self.root.after(3000, self._record_lig_coord)

    def _record_lig_coord(self):
        import pyautogui
        pos = pyautogui.position()
        logger.info(f"Recorded Lig: {pos}")
        
        save_coordinates((self._temp_var.x, self._temp_var.y), (pos.x, pos.y))
        self.coords_loaded = load_coordinates()
        
        self.btn_upd_coords.config(text="更新完毕", bg=THEME["green"])
        self.root.after(1000, lambda: self.btn_upd_coords.config(text="更新坐标", bg=THEME["accent"]))
        
        # update display
        var_pos, lig_pos = get_coordinates()
        self.l_coord_status.config(text=f"V:{var_pos.x},{var_pos.y} L:{lig_pos.x},{lig_pos.y}")

    def emergency_stop(self):
        if self.strategy.running:
            self.strategy.stop()
            self.root.after(0, lambda: self.btn_toggle.config(text="START", bg=THEME["green"]))

    # ========== Callbacks ==========
    
    def _on_lig_price_update(self, bid, ask):
        """Lighter WS callback."""
        try:
            # logger.info(f"Received Lig update: {bid} / {ask}")
            if bid and ask:
                self.lig_bbo["bid"] = Decimal(str(bid))
                self.lig_bbo["ask"] = Decimal(str(ask))
                self.strategy.update_lig_prices(self.lig_bbo["bid"], self.lig_bbo["ask"])
                
                # Visual feed indicator (could add a timestamp to UI if needed)
                self.last_lig_update = time.time()
                
        except Exception as e:
            logger.error(f"Error in Lig callback: {e}")

    def _on_var_price_update(self):
        prices = data_feeds.get_prices()
        if prices:
            self.var_bbo["bid"] = Decimal(str(prices.get("var", {}).get("bid", 0)))
            self.var_bbo["ask"] = Decimal(str(prices.get("var", {}).get("ask", 0)))
            self.strategy.update_var_prices(self.var_bbo["bid"], self.var_bbo["ask"])

    def _on_trade(self, data):
        """Trade callback."""
        trades = self.strategy.state.trades_executed
        max_t = self.strategy.config.max_trades
        self.root.after(0, lambda: self.l_trades.config(text=f"{trades}/{max_t}"))
        subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
        
        # Check max trades
        if trades >= max_t:
            self.strategy.stop()
            self.root.after(0, lambda: self.btn_toggle.config(text="START", bg=THEME["green"]))
            logger.info("🔔 已达到最大交易次数，自动停止")

    # ========== UI Update Loop ==========
    
    def _update_ui(self):
        # Update labels
        if self.lig_bbo["bid"]:
            self.l_lig_bid.config(text=f"Bid: {self.lig_bbo['bid']:.2f}")
            self.l_lig_ask.config(text=f"Ask: {self.lig_bbo['ask']:.2f}")
            
        if self.var_bbo["bid"]:
            self.l_var_bid.config(text=f"Bid: {self.var_bbo['bid']:.2f}")
            self.l_var_ask.config(text=f"Ask: {self.var_bbo['ask']:.2f}")
            
        # Spreads
        # Var Sell (Buy Lig): Var_Bid - Lig_Ask
        if self.var_bbo["bid"] and self.lig_bbo["ask"]:
            s_vs = self.var_bbo["bid"] - self.lig_bbo["ask"]
            c = THEME["green"] if s_vs > 0 else THEME["red"]
            self.l_spread_vs.config(text=f"{s_vs:+.2f}", fg=c)
            
        # Lig Sell (Buy Var): Lig_Bid - Var_Ask
        if self.lig_bbo["bid"] and self.var_bbo["ask"]:
            s_ls = self.lig_bbo["bid"] - self.var_bbo["ask"]
            c = THEME["green"] if s_ls > 0 else THEME["red"]
            self.l_spread_ls.config(text=f"{s_ls:+.2f}", fg=c)
            
        self.root.after(200, self._update_ui)

    # ========== Async ==========

    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main_task())

    async def _main_task(self):
        # Initialize Lighter Client in async loop
        if not self.lig_client:
            try:
                self.lig_client = LighterClient()
                markets = await self.lig_client.fetch_markets()
                logger.info(f"Lighter client initialized with {len(markets)} markets")
            except Exception as e:
                logger.error(f"Failed to init Lighter client: {e}")

        # Start data feeds
        tasks = [data_feeds.monitor_variational()]
        
        # Initial Lighter sub
        if self.lig_client:
            pair = self.lig_pairs[self.current_pair_idx]
            await self._update_lig_subscription(pair["lig_symbol"])
            
        await asyncio.gather(*tasks)

    async def _update_lig_subscription(self, symbol: str):
        # Claim this switch with a unique serial number
        self._sub_serial += 1
        my_serial = self._sub_serial

        # Stop existing monitor
        if self.lig_monitor:
            await self.lig_monitor.disconnect()
            self.lig_monitor = None

        if self.lig_task:
            self.lig_task.cancel()
            try:
                await self.lig_task
            except asyncio.CancelledError:
                pass
            self.lig_task = None

        # Abort if a newer switch was requested while we were awaiting above
        if my_serial != self._sub_serial:
            logger.debug(f"Subscription switch to {symbol} superseded, aborting")
            return

        if self.lig_client:
            logger.info(f"Resolving market ID for {symbol}...")
            market_id = await self.lig_client.get_market_id(symbol)

            # Abort again after the async get_market_id call
            if my_serial != self._sub_serial:
                logger.debug(f"Subscription switch to {symbol} superseded after ID lookup, aborting")
                return

            if market_id is not None:
                logger.info(f"Subscribing to Lighter market {market_id}")
                self.lig_monitor = self.lig_client.create_monitor(market_id, self._on_lig_price_update)
                self.lig_task = asyncio.create_task(self.lig_monitor.connect())
            else:
                logger.error(f"Could not find market ID for {symbol}")

if __name__ == "__main__":
    app = LigHedgeWindow()
    app.root.mainloop()
