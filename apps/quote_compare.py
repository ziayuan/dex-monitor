"""
Quote Compare Floating Window
Side-by-side comparison of /api/quotes/simple vs /api/quotes/indicative
"""
import tkinter as tk
import threading
import asyncio
import os
import sys
from pathlib import Path

# Add project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from curl_cffi.requests import AsyncSession

# --- Config ---
VR_TOKEN = os.getenv("VR_TOKEN", "")
POLL_INTERVAL = 1.0  # seconds
QTY = "0.01"

PAIRS = ["BTC", "ETH", "SOL"]  # Default pairs to show
CURRENT_IDX = 0

# --- Theme ---
BG = "#1a1b26"
BG_CARD = "#24283b"
FG = "#c0caf5"
FG_DIM = "#565f89"
GREEN = "#9ece6a"
RED = "#f7768e"
PURPLE = "#bb9af7"
ORANGE = "#ff9e64"
BLUE = "#7aa2f7"
FONT = ("SF Mono", 12)
FONT_SM = ("SF Mono", 10)
FONT_LG = ("SF Mono", 14, "bold")


class QuoteCompareWindow:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Quote Compare")
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.95)
        self.root.configure(bg=BG)
        self.root.geometry("500x350+200+200")
        
        self.current_pair = PAIRS[CURRENT_IDX]
        self.simple_data = {}
        self.indic_data = {}
        
        self._build_ui()
        
        # Async loop
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, daemon=True)
        self.thread.start()
        
        self.root.after(300, self._refresh_ui)
        self.root.mainloop()
    
    def _build_ui(self):
        # Header
        hdr = tk.Frame(self.root, bg=BG)
        hdr.pack(fill=tk.X, padx=10, pady=(8, 4))
        
        # Close button
        close_btn = tk.Label(hdr, text="●", fg=RED, bg=BG, font=("SF Mono", 14), cursor="hand2")
        close_btn.pack(side=tk.LEFT)
        close_btn.bind("<Button-1>", lambda e: self.root.quit())
        
        tk.Label(hdr, text="Quote Compare", bg=BG, fg=FG_DIM, font=FONT_SM).pack(side=tk.LEFT, padx=10)
        
        # Pair selector
        self.pair_btn = tk.Label(hdr, text=self.current_pair, bg=BG_CARD, fg=FG, 
                                 font=FONT_SM, padx=8, pady=2, cursor="hand2")
        self.pair_btn.pack(side=tk.RIGHT)
        self.pair_btn.bind("<Button-1>", self._cycle_pair)
        
        # Qty display
        self.qty_label = tk.Label(hdr, text=f"qty={QTY}", bg=BG, fg=FG_DIM, font=FONT_SM)
        self.qty_label.pack(side=tk.RIGHT, padx=5)
        
        # --- Main card ---
        card = tk.Frame(self.root, bg=BG_CARD, padx=12, pady=10)
        card.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)
        
        # Column headers
        hdr_row = tk.Frame(card, bg=BG_CARD)
        hdr_row.pack(fill=tk.X, pady=(0, 6))
        tk.Label(hdr_row, text="", bg=BG_CARD, fg=FG_DIM, font=FONT_SM, width=10, anchor="w").pack(side=tk.LEFT)
        tk.Label(hdr_row, text="Simple", bg=BG_CARD, fg=BLUE, font=FONT_SM, width=16, anchor="e").pack(side=tk.LEFT)
        tk.Label(hdr_row, text="Indicative", bg=BG_CARD, fg=PURPLE, font=FONT_SM, width=16, anchor="e").pack(side=tk.LEFT)
        tk.Label(hdr_row, text="Diff", bg=BG_CARD, fg=FG_DIM, font=FONT_SM, width=10, anchor="e").pack(side=tk.LEFT)
        
        # Rows: bid, ask, mark, spread, slip
        self.rows = {}
        for label in ["Bid", "Ask", "Mark", "Spread", "Slip(buy)"]:
            row = tk.Frame(card, bg=BG_CARD)
            row.pack(fill=tk.X, pady=1)
            
            color = GREEN if label == "Bid" else RED if label == "Ask" else ORANGE if "Slip" in label else FG_DIM
            tk.Label(row, text=label, bg=BG_CARD, fg=color, font=FONT_SM, width=10, anchor="w").pack(side=tk.LEFT)
            
            l_simple = tk.Label(row, text="--", bg=BG_CARD, fg=BLUE, font=FONT, width=16, anchor="e")
            l_simple.pack(side=tk.LEFT)
            l_indic = tk.Label(row, text="--", bg=BG_CARD, fg=PURPLE, font=FONT, width=16, anchor="e")
            l_indic.pack(side=tk.LEFT)
            l_diff = tk.Label(row, text="--", bg=BG_CARD, fg=FG_DIM, font=FONT_SM, width=10, anchor="e")
            l_diff.pack(side=tk.LEFT)
            
            self.rows[label] = (l_simple, l_indic, l_diff)
        
        # Separator
        tk.Frame(card, bg=FG_DIM, height=1).pack(fill=tk.X, pady=8)
        
        # Timestamp
        self.l_time = tk.Label(card, text="", bg=BG_CARD, fg=FG_DIM, font=FONT_SM)
        self.l_time.pack(anchor="w")
        
        # Status
        self.l_status = tk.Label(card, text="Connecting...", bg=BG_CARD, fg=ORANGE, font=FONT_SM)
        self.l_status.pack(anchor="w")
        
        # Drag support
        for w in [self.root, hdr]:
            w.bind("<Button-1>", self._start_move)
            w.bind("<B1-Motion>", self._do_move)
    
    def _start_move(self, e):
        self._drag_x = e.x
        self._drag_y = e.y
    
    def _do_move(self, e):
        x = self.root.winfo_x() + (e.x - self._drag_x)
        y = self.root.winfo_y() + (e.y - self._drag_y)
        self.root.geometry(f"+{x}+{y}")
    
    def _cycle_pair(self, e=None):
        global CURRENT_IDX
        CURRENT_IDX = (CURRENT_IDX + 1) % len(PAIRS)
        self.current_pair = PAIRS[CURRENT_IDX]
        self.pair_btn.config(text=self.current_pair)
    
    def _run_loop(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._poll_forever())
    
    async def _poll_forever(self):
        async with AsyncSession() as session:
            while True:
                pair = self.current_pair
                payload = {
                    "instrument": {
                        "underlying": pair,
                        "funding_interval_s": 3600,
                        "settlement_asset": "USDC",
                        "instrument_type": "perpetual_future",
                    },
                    "qty": QTY,
                }
                
                try:
                    # Fetch both simultaneously
                    task_s = session.post(
                        "https://omni.variational.io/api/quotes/simple",
                        json=payload, impersonate="chrome116", timeout=5,
                    )
                    task_i = session.post(
                        "https://omni.variational.io/api/quotes/indicative",
                        headers={
                            "cookie": f"vr-token={VR_TOKEN}",
                            "origin": "https://omni.variational.io",
                        },
                        json=payload, impersonate="chrome116", timeout=5, verify=False,
                    )
                    
                    resp_s, resp_i = await asyncio.gather(task_s, task_i)
                    
                    if resp_s.status_code == 200:
                        self.simple_data = resp_s.json()
                    if resp_i.status_code == 200:
                        self.indic_data = resp_i.json()
                    elif resp_i.status_code == 401:
                        self.indic_data = {"error": "Token expired"}
                    
                except Exception as e:
                    self.indic_data = {"error": str(e)[:50]}
                
                await asyncio.sleep(POLL_INTERVAL)
    
    def _refresh_ui(self):
        sd = self.simple_data
        ind = self.indic_data
        
        if "error" in ind:
            self.l_status.config(text=f"⚠ Indicative: {ind['error']}", fg=RED)
        elif sd and ind:
            self.l_status.config(text="✅ Both endpoints OK", fg=GREEN)
        
        if sd and "bid" in sd:
            s_bid = float(sd["bid"])
            s_ask = float(sd["ask"]) 
            s_mark = float(sd["mark_price"])
            s_spread = s_ask - s_bid
            s_slip = (s_ask - s_mark) / s_mark * 100 if s_mark else 0
            
            self.rows["Bid"][0].config(text=f"{s_bid:.2f}")
            self.rows["Ask"][0].config(text=f"{s_ask:.2f}")
            self.rows["Mark"][0].config(text=f"{s_mark:.2f}")
            self.rows["Spread"][0].config(text=f"{s_spread:.2f}")
            self.rows["Slip(buy)"][0].config(text=f"{s_slip:.4f}%")
            
            ts = sd.get("timestamp", "")[:19]
            self.l_time.config(text=f"Time: {ts}")
        
        if ind and "bid" in ind:
            i_bid = float(ind["bid"])
            i_ask = float(ind["ask"])
            i_mark = float(ind["mark_price"])
            i_spread = i_ask - i_bid
            i_slip = (i_ask - i_mark) / i_mark * 100 if i_mark else 0
            
            self.rows["Bid"][1].config(text=f"{i_bid:.2f}")
            self.rows["Ask"][1].config(text=f"{i_ask:.2f}")
            self.rows["Mark"][1].config(text=f"{i_mark:.2f}")
            self.rows["Spread"][1].config(text=f"{i_spread:.2f}")
            self.rows["Slip(buy)"][1].config(text=f"{i_slip:.4f}%")
            
            # Diffs
            if sd and "bid" in sd:
                s_bid = float(sd["bid"])
                s_ask = float(sd["ask"])
                s_mark = float(sd["mark_price"])
                
                for label, s_val, i_val in [
                    ("Bid", s_bid, i_bid),
                    ("Ask", s_ask, i_ask),
                    ("Mark", s_mark, i_mark),
                    ("Spread", s_ask - s_bid, i_spread),
                ]:
                    diff = i_val - s_val
                    color = GREEN if abs(diff) < 0.01 else ORANGE if abs(diff) < 1 else RED
                    self.rows[label][2].config(text=f"{diff:+.2f}", fg=color)
                
                # Slip diff in bps
                s_slip_v = (s_ask - s_mark) / s_mark * 10000
                i_slip_v = (i_ask - i_mark) / i_mark * 10000
                slip_diff = i_slip_v - s_slip_v
                self.rows["Slip(buy)"][2].config(text=f"{slip_diff:+.2f}bp", 
                                                  fg=GREEN if abs(slip_diff) < 0.1 else ORANGE)
        
        self.root.after(300, self._refresh_ui)


if __name__ == "__main__":
    if not VR_TOKEN:
        print("ERROR: VR_TOKEN not found in .env")
        sys.exit(1)
    QuoteCompareWindow()
