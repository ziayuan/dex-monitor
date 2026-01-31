"""Refactored Floating Window Application.

Uses core and strategies modules for shared functionality.
~350 lines (down from 649)
"""
import asyncio
import json
import subprocess
import threading
import time
import tkinter as tk

import websockets

# Import from core modules
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.config import load_config
from core.clicker import (
    load_coordinates, perform_clicks, check_mouse_movement,
    reset_interrupt, set_interrupt_callback
)
from core import data_feeds
from strategies.var_paradex import DualSpreadStrategy


# Load configuration
CFG = load_config()

# UI Theme (Apple Style)
THEME = {
    "bg_window": "#1C1C1E",
    "bg_card":   "#2C2C2E",
    "bg_input":  "#3A3A3C",
    "fg_primary": "#FFFFFF",
    "fg_secondary": "#98989D",
    "accent":    "#0A84FF",
    "green":     "#32D74B",
    "red":       "#FF453A",
    "yellow":    "#FFD60A",
}
FONT_TITLE = ("Helvetica Neue", 13, "bold")
FONT_BODY = ("Helvetica Neue", 12)
FONT_SMALL = ("Helvetica Neue", 10)
FONT_MONO = ("Monaco", 11)


class FloatingWindow:
    def __init__(self):
        # Initialize data feeds
        data_feeds.init(CFG)
        data_feeds.set_price_callback(self.on_price_update)
        
        # Strategy
        self.strategy = DualSpreadStrategy()
        
        # Coordinates
        self.coords_loaded = load_coordinates()
        set_interrupt_callback(self.stop_strategy_safety)
        
        # Root window
        self.root = tk.Tk()
        self.root.title("Zero Latency Monitor")
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", 0.95)
        self.root.configure(bg=THEME["bg_window"])
        self.root.geometry("400x320+100+100")
        
        # Create UI
        self._create_header()
        self._create_symbol_view()
        self._create_strategy_panel()
        
        # Drag bindings
        for w in [self.root, self.lbl_title, self.header]:
            w.bind("<Button-1>", self._start_move)
            w.bind("<B1-Motion>", self._do_move)
        
        # Async loop
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_async_loop, daemon=True)
        self.thread.start()
        
        # UI update loop
        self.root.after(200, self._update_ui)
    
    def _create_header(self):
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
        self.lbl_title = tk.Label(self.header, text="Monitor", bg=THEME["bg_window"], 
                                   fg=THEME["fg_secondary"], font=FONT_SMALL)
        self.lbl_title.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Pair selector
        self.pair_list = list(data_feeds.PAIRS().keys())
        self.pair_index = 0
        self.pair_btn = tk.Label(self.header, text=data_feeds.get_current_symbol(),
                                  bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                  font=FONT_SMALL, padx=8, pady=2, cursor="hand2")
        self.pair_btn.pack(side=tk.RIGHT)
        self.pair_btn.bind("<Button-1>", self._cycle_pair)
    
    def _create_symbol_view(self):
        self.card = tk.Frame(self.root, bg=THEME["bg_card"], pady=10, padx=10)
        self.card.pack(fill=tk.X, padx=10, pady=5)
        
        self.l_sym = tk.Label(self.card, text=data_feeds.get_current_symbol(),
                               bg=THEME["bg_card"], fg=THEME["fg_primary"], font=FONT_TITLE)
        self.l_sym.pack(anchor="w")
        
        g = tk.Frame(self.card, bg=THEME["bg_card"])
        g.pack(fill=tk.X, pady=(5, 0))
        
        tk.Label(g, text="Var", bg=THEME["bg_card"], fg=THEME["fg_secondary"], 
                 font=FONT_SMALL).grid(row=0, column=0, sticky="w")
        self.l_var = tk.Label(g, text="-", bg=THEME["bg_card"], fg=THEME["fg_primary"], font=FONT_MONO)
        self.l_var.grid(row=0, column=1, sticky="w", padx=(5, 15))
        
        tk.Label(g, text="Par", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).grid(row=0, column=2, sticky="w")
        self.l_par = tk.Label(g, text="-", bg=THEME["bg_card"], fg=THEME["fg_primary"], font=FONT_MONO)
        self.l_par.grid(row=0, column=3, sticky="w", padx=5)
        
        s = tk.Frame(self.card, bg=THEME["bg_card"])
        s.pack(fill=tk.X, pady=(8, 0))
        
        sa = tk.Frame(s, bg=THEME["bg_card"])
        sa.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(sa, text="Var卖", bg=THEME["bg_card"], fg=THEME["fg_secondary"], font=FONT_SMALL).pack(anchor="w")
        self.l_s1 = tk.Label(sa, text="-", bg=THEME["bg_card"], fg=THEME["fg_primary"], font=FONT_BODY)
        self.l_s1.pack(anchor="w")
        
        sb = tk.Frame(s, bg=THEME["bg_card"])
        sb.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(sb, text="Par卖", bg=THEME["bg_card"], fg=THEME["fg_secondary"], font=FONT_SMALL).pack(anchor="w")
        self.l_s2 = tk.Label(sb, text="-", bg=THEME["bg_card"], fg=THEME["fg_primary"], font=FONT_BODY)
        self.l_s2.pack(anchor="w")
    
    def _create_strategy_panel(self):
        self.strat_frame = tk.Frame(self.root, bg=THEME["bg_card"], pady=8, padx=8)
        self.strat_frame.pack(fill=tk.X, padx=10, pady=5)
        
        # Row 1: Var卖
        r1 = tk.Frame(self.strat_frame, bg=THEME["bg_card"])
        r1.pack(fill=tk.X, pady=2)
        
        self.var_check_a = tk.IntVar(value=1)
        tk.Checkbutton(r1, text="Var卖", variable=self.var_check_a, 
                       bg=THEME["bg_card"], fg=THEME["fg_primary"],
                       selectcolor=THEME["bg_input"], font=FONT_SMALL,
                       command=self._update_config).pack(side=tk.LEFT)
        
        tk.Label(r1, text=">=", bg=THEME["bg_card"], fg=THEME["fg_secondary"], 
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(5, 2))
        self.ent_a = tk.Entry(r1, width=5, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                              bd=0, font=FONT_MONO, justify="center")
        self.ent_a.insert(0, "5.0")
        self.ent_a.pack(side=tk.LEFT)
        self.ent_a.bind("<KeyRelease>", lambda e: self._update_config())
        
        self.l_val_a = tk.Label(r1, text="(--)", bg=THEME["bg_card"], 
                                 fg=THEME["fg_secondary"], font=FONT_SMALL)
        self.l_val_a.pack(side=tk.LEFT, padx=5)
        
        # Row 2: Par卖
        r2 = tk.Frame(self.strat_frame, bg=THEME["bg_card"])
        r2.pack(fill=tk.X, pady=2)
        
        self.var_check_b = tk.IntVar(value=0)
        tk.Checkbutton(r2, text="Par卖", variable=self.var_check_b,
                       bg=THEME["bg_card"], fg=THEME["fg_primary"],
                       selectcolor=THEME["bg_input"], font=FONT_SMALL,
                       command=self._update_config).pack(side=tk.LEFT)
        
        tk.Label(r2, text=">=", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(5, 2))
        self.ent_b = tk.Entry(r2, width=5, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                              bd=0, font=FONT_MONO, justify="center")
        self.ent_b.insert(0, "5.0")
        self.ent_b.pack(side=tk.LEFT)
        self.ent_b.bind("<KeyRelease>", lambda e: self._update_config())
        
        self.l_val_b = tk.Label(r2, text="(--)", bg=THEME["bg_card"],
                                 fg=THEME["fg_secondary"], font=FONT_SMALL)
        self.l_val_b.pack(side=tk.LEFT, padx=5)
        
        # Row 3: Controls
        r3 = tk.Frame(self.strat_frame, bg=THEME["bg_card"])
        r3.pack(fill=tk.X, pady=(6, 0))
        
        tk.Label(r3, text="Max", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT)
        self.ent_max = tk.Entry(r3, width=3, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                                bd=0, font=FONT_MONO, justify="center")
        self.ent_max.insert(0, "10")
        self.ent_max.pack(side=tk.LEFT, padx=2)
        
        tk.Label(r3, text="Cd", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(5, 0))
        self.ent_cd = tk.Entry(r3, width=3, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                               bd=0, font=FONT_MONO, justify="center")
        self.ent_cd.insert(0, "5.0")
        self.ent_cd.pack(side=tk.LEFT, padx=2)
        
        tk.Label(r3, text="Cf", bg=THEME["bg_card"], fg=THEME["fg_secondary"],
                 font=FONT_SMALL).pack(side=tk.LEFT, padx=(5, 0))
        self.ent_cf = tk.Entry(r3, width=2, bg=THEME["bg_input"], fg=THEME["fg_primary"],
                               bd=0, font=FONT_MONO, justify="center")
        self.ent_cf.insert(0, "2")
        self.ent_cf.pack(side=tk.LEFT, padx=2)
        
        self.lbl_counter = tk.Label(r3, text="0/10", bg=THEME["bg_card"],
                                     fg=THEME["fg_secondary"], font=FONT_SMALL)
        self.lbl_counter.pack(side=tk.LEFT, padx=10)
        
        self.btn_toggle = tk.Label(r3, text="START", bg=THEME["green"], fg="white",
                                    font=FONT_SMALL, width=8, pady=3, cursor="hand2")
        self.btn_toggle.pack(side=tk.RIGHT)
        self.btn_toggle.bind("<Button-1>", lambda e: self._toggle_strategy())
        
        self._update_config()
    
    def _cycle_pair(self, event):
        self.pair_index = (self.pair_index + 1) % len(self.pair_list)
        sym = self.pair_list[self.pair_index]
        data_feeds.set_current_symbol(sym)
        self.pair_btn.config(text=sym)
        self.l_sym.config(text=sym)
        self.l_var.config(text="-")
        self.l_par.config(text="-")
    
    def _start_move(self, event):
        self.x, self.y = event.x, event.y
    
    def _do_move(self, event):
        x = self.root.winfo_x() + (event.x - self.x)
        y = self.root.winfo_y() + (event.y - self.y)
        self.root.geometry(f"+{x}+{y}")
    
    def _update_config(self):
        try:
            thresh_a = float(self.ent_a.get())
        except:
            thresh_a = 5.0
        try:
            thresh_b = float(self.ent_b.get())
        except:
            thresh_b = 5.0
        try:
            max_clicks = int(self.ent_max.get())
        except:
            max_clicks = 10
        try:
            cooldown = float(self.ent_cd.get())
        except:
            cooldown = 5.0
        try:
            confirm = int(self.ent_cf.get())
        except:
            confirm = 2
        
        self.strategy.configure(
            enable_a=self.var_check_a.get() == 1,
            threshold_a=thresh_a,
            enable_b=self.var_check_b.get() == 1,
            threshold_b=thresh_b,
            max_clicks=max_clicks,
            cooldown=cooldown,
            confirm_count=max(1, confirm)
        )
    
    def _toggle_strategy(self):
        if not self.strategy.running:
            if not self.coords_loaded:
                self.coords_loaded = load_coordinates()
            if not self.coords_loaded:
                return
            
            self._update_config()
            reset_interrupt()
            self.strategy.start()
            self.btn_toggle.config(text="STOP", bg=THEME["red"])
            self._update_counter()
        else:
            self.stop_strategy()
    
    def stop_strategy(self):
        self.strategy.stop()
        self.btn_toggle.config(text="START", bg=THEME["green"])
    
    def stop_strategy_safety(self):
        self.strategy.stop()
        self.root.after(0, lambda: self.btn_toggle.config(text="START", bg=THEME["green"]))
    
    def _update_counter(self):
        self.lbl_counter.config(text=f"{self.strategy.clicks_performed}/{self.strategy.max_clicks}")
    
    def on_price_update(self):
        """Called when prices update - check strategy."""
        if not self.strategy.running:
            return
        
        prices = data_feeds.get_prices()
        triggered = self.strategy.check(prices)
        
        if triggered:
            source_name = "Var卖" if triggered == "a" else "Par卖"
            sig_a, sig_b = self.strategy.get_signals(prices)
            sig = sig_a if triggered == "a" else sig_b
            print(f"⚡ [{source_name}] 确认触发: {sig:.2f}")
            
            perform_clicks()
            self.strategy.on_executed(triggered)
            self.root.after(0, self._update_counter)
            
            if self.strategy.clicks_performed >= self.strategy.max_clicks:
                self.stop_strategy()
                subprocess.Popen(["afplay", "/System/Library/Sounds/Glass.aiff"])
                print("🔔 已达到最大点击次数！")
    
    def _update_ui(self):
        prices = data_feeds.get_prices()
        if prices:
            vb = prices.get("var", {}).get("bid", 0)
            va = prices.get("var", {}).get("ask", 0)
            pb = prices.get("para", {}).get("bid", 0)
            pa = prices.get("para", {}).get("ask", 0)
            
            self.l_var.config(text=f"{vb:.2f} / {va:.2f}")
            self.l_par.config(text=f"{pb:.2f} / {pa:.2f}")
            
            s1 = vb - pa if vb and pa else 0
            s2 = pb - va if pb and va else 0
            
            self.l_s1.config(text=f"{s1:+.2f}", fg=THEME["green"] if s1 > 0 else THEME["red"])
            self.l_s2.config(text=f"{s2:+.2f}", fg=THEME["green"] if s2 > 0 else THEME["red"])
            
            self.l_val_a.config(text=f"({s1:+.2f})")
            self.l_val_b.config(text=f"({s2:+.2f})")
        
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, self._update_ui)
    
    def _run_async_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main_task())
    
    async def _main_task(self):
        try:
            server = await websockets.serve(self._ws_handler, "localhost", CFG.local_ws_port)
            print(f"Server Started :{CFG.local_ws_port}")
            await asyncio.gather(
                data_feeds.monitor_variational(),
                data_feeds.monitor_paradex(),
                self._broadcast_loop()
            )
        except Exception as e:
            print(f"Async Loop Error: {e}")
    
    async def _ws_handler(self, websocket):
        data_feeds._clients = getattr(data_feeds, "_clients", set())
        data_feeds._clients.add(websocket)
        try:
            await websocket.wait_closed()
        finally:
            data_feeds._clients.discard(websocket)
    
    async def _broadcast_loop(self):
        clients = getattr(data_feeds, "_clients", set())
        while True:
            if clients:
                try:
                    sym = data_feeds.get_current_symbol()
                    prices = data_feeds.get_prices(sym)
                    v_bid = prices.get("var", {}).get("bid", 0)
                    v_ask = prices.get("var", {}).get("ask", 0)
                    p_bid = prices.get("para", {}).get("bid", 0)
                    p_ask = prices.get("para", {}).get("ask", 0)
                    spread_a = v_bid - p_ask if v_bid and p_ask else 0.0
                    spread_b = p_bid - v_ask if p_bid and v_ask else 0.0
                    msg = json.dumps({"pairs": [{
                        "symbol": sym, "spread_a": spread_a, "spread_b": spread_b,
                        "var_bid": v_bid, "var_ask": v_ask, "para_bid": p_bid, "para_ask": p_ask
                    }]})
                    for ws in list(clients):
                        try:
                            await ws.send(msg)
                        except:
                            pass
                except:
                    pass
            await asyncio.sleep(0.1)


if __name__ == "__main__":
    app = FloatingWindow()
    app.root.mainloop()
