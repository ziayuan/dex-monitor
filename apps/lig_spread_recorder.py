import asyncio
import json
import sqlite3
import time
import sys
from pathlib import Path

# Add parent to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import websockets
from loguru import logger
from core.config import load_config
import core.data_feeds as data_feeds
from exchanges.lighter import LighterClient

DB_PATH = Path("logs/lig_spreads.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS spreads (
                timestamp REAL,
                pair TEXT,
                var_bid REAL,
                var_ask REAL,
                lig_bid REAL,
                lig_ask REAL,
                spread_vs REAL,
                spread_ls REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_pair_time ON spreads (pair, timestamp)")

class SpreadRecorder:
    def __init__(self):
        self.config = load_config()
        self.pairs = self.config.get("lighter", {}).get("pairs", [
            {"symbol": "BTC-USD", "var_symbol": "BTC-USD", "lig_symbol": "BTC"},
            {"symbol": "ETH-USD", "var_symbol": "ETH-USD", "lig_symbol": "ETH"},
        ])
        
        self.prices = {
            p["symbol"]: {"var_bid": 0, "var_ask": 0, "lig_bid": 0, "lig_ask": 0}
            for p in self.pairs
        }
        self.last_insert = {p["symbol"]: 0.0 for p in self.pairs}
        
        self.lig_client = LighterClient()
        self.lig_monitors = []
        self.active_ws = set()
        
    def _save_spread(self, pair_symbol: str):
        prices = self.prices[pair_symbol]
        var_bid = prices["var_bid"]
        var_ask = prices["var_ask"]
        lig_bid = prices["lig_bid"]
        lig_ask = prices["lig_ask"]
        
        spread_vs = None
        spread_ls = None

        if var_bid and lig_ask:
            spread_vs = var_bid - lig_ask # Var sell, Lig buy
        if lig_bid and var_ask:
            spread_ls = lig_bid - var_ask # Lig sell, Var buy
            
        if spread_vs is not None and spread_ls is not None:
            ts = time.time()
            
            # Broadcast to WS (Throttled to 0.2s for snappy real-time UI like the floating window)
            last_ws = getattr(self, "last_ws", {})
            if not hasattr(self, "last_ws"): self.last_ws = last_ws
            
            if ts - last_ws.get(pair_symbol, 0) >= 0.2:
                last_ws[pair_symbol] = ts
                # Broadcast to WS
                msg = json.dumps({
                    "type": "update",
                    "data": {
                        "timestamp": ts,
                        "pair": pair_symbol,
                        "var_bid": var_bid,
                        "var_ask": var_ask,
                        "lig_bid": lig_bid,
                        "lig_ask": lig_ask,
                        "spread_vs": spread_vs,
                        "spread_ls": spread_ls,
                        "is_live": True
                    }
                })
                asyncio.create_task(self._broadcast(msg))
            
            # Throttle DB writes to 1 per second per pair to avoid overload and huge DBs
            if ts - self.last_insert.get(pair_symbol, 0) < 1.0:
                return
            self.last_insert[pair_symbol] = ts
            
            # Write to DB
            with sqlite3.connect(DB_PATH) as conn:
                conn.execute(
                    "INSERT INTO spreads VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (ts, pair_symbol, var_bid, var_ask, lig_bid, lig_ask, spread_vs, spread_ls)
                )
    async def _broadcast(self, msg: str):
        if self.active_ws:
            disconnected = set()
            for ws in self.active_ws:
                try:
                    await ws.send(msg)
                except Exception:
                    disconnected.add(ws)
            for ws in disconnected:
                self.active_ws.remove(ws)

    def _on_lig_update(self, pair_symbol):
        def callback(bid, ask):
            if bid and ask:
                self.prices[pair_symbol]["lig_bid"] = float(bid)
                self.prices[pair_symbol]["lig_ask"] = float(ask)
                self._save_spread(pair_symbol)
        return callback

    async def ws_handler(self, websocket, path=None):
        """Handle a frontend WebSocket connection: send history then stream live."""
        self.active_ws.add(websocket)
        logger.info(f"Chart client connected ({len(self.active_ws)} total)")
        try:
            # Send 12h history on connect
            cutoff = time.time() - 12 * 3600
            with sqlite3.connect(DB_PATH) as conn:
                rows = conn.execute(
                    "SELECT timestamp, pair, AVG(var_bid), AVG(var_ask), AVG(lig_bid), AVG(lig_ask), AVG(spread_vs), AVG(spread_ls) "
                    "FROM spreads WHERE timestamp > ? "
                    "GROUP BY pair, CAST(timestamp / 10 AS INT) "
                    "ORDER BY timestamp",
                    (cutoff,)
                ).fetchall()
            
            history = [
                {
                    "timestamp": r[0], "pair": r[1],
                    "var_bid": r[2], "var_ask": r[3],
                    "lig_bid": r[4], "lig_ask": r[5],
                    "spread_vs": r[6], "spread_ls": r[7]
                }
                for r in rows
            ]
            await websocket.send(json.dumps({"type": "history", "data": history}))
            
            # Keep alive until disconnect
            async for _ in websocket:
                pass
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self.active_ws.discard(websocket)
            logger.info(f"Chart client disconnected ({len(self.active_ws)} total)")

    async def _broadcast_stats(self):
        """Periodically broadcast 1min, 30min, 1h, 2h average spreads for all pairs to power the dashboard."""
        while True:
            await asyncio.sleep(5)  # Update dashboard stats every 5 seconds
            if not self.active_ws:
                continue
                
            now = time.time()
            stats = {}
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    for pair in self.prices.keys():
                        stats[pair] = {}
                        # minutes-based windows: 1min, 30min; hour-based: 1h, 2h
                        windows = {"1m": 60, "30m": 1800, "1h": 3600, "2h": 7200}
                        for label, seconds in windows.items():
                            cutoff = now - seconds
                            row = conn.execute(
                                """
                                SELECT 
                                    AVG(spread_vs), 
                                    AVG(spread_ls),
                                    AVG(CASE WHEN var_bid > 0 THEN (spread_vs / var_bid) * 10000 ELSE 0 END),
                                    AVG(CASE WHEN lig_bid > 0 THEN (spread_ls / lig_bid) * 10000 ELSE 0 END)
                                FROM spreads 
                                WHERE pair = ? AND timestamp > ?
                                """,
                                (pair, cutoff)
                            ).fetchone()
                            
                            stats[pair][label] = {
                                "vs_abs": row[0] if row[0] is not None else 0,
                                "ls_abs": row[1] if row[1] is not None else 0,
                                "vs_bps": row[2] if row[2] is not None else 0,
                                "ls_bps": row[3] if row[3] is not None else 0
                            }
                
                msg = json.dumps({"type": "stats", "data": stats})
                asyncio.create_task(self._broadcast(msg))
            except Exception as e:
                logger.error(f"Error computing stats: {e}")

    def _on_var_update(self):
        """Called when data_feeds updates Variational prices — sync into self.prices."""
        all_prices = data_feeds.get_all_prices()
        for p in self.pairs:
            sym = p["symbol"]
            if sym in all_prices and "var" in all_prices[sym]:
                var = all_prices[sym]["var"]
                if var.get("bid") and var.get("ask"):
                    self.prices[sym]["var_bid"] = float(var["bid"])
                    self.prices[sym]["var_ask"] = float(var["ask"])
                    self._save_spread(sym)

    async def start(self):
        logger.info("Starting Lig-Var Spread Recorder")
        init_db()
        
        # Initialize data_feeds configuration
        data_feeds.init(self.config)
        
        # Register callback so Var price updates flow into our prices dict
        data_feeds.set_price_callback(self._on_var_update)
        
        # Start Lighter Monitors
        await self.lig_client.fetch_markets()
        for p in self.pairs:
            sym = p["symbol"]
            lig_sym = p["lig_symbol"]
            market_id = await self.lig_client.get_market_id(lig_sym)
            if market_id is not None:
                monitor = self.lig_client.create_monitor(market_id, self._on_lig_update(sym))
                self.lig_monitors.append(monitor)
                asyncio.create_task(monitor.connect())
                logger.info(f"Started Lighter WS monitor for {sym} ({lig_sym})")
            else:
                logger.error(f"Could not resolve Lighter market ID for {lig_sym}")

        # Start Variational direct API polling (all pairs)
        asyncio.create_task(data_feeds.monitor_variational_all())

        # Start periodic stats broadcaster
        asyncio.create_task(self._broadcast_stats())

        # Start UI WebSocket Server
        try:
            server = await websockets.serve(self.ws_handler, "0.0.0.0", 8765)
            logger.info("WebSocket server listening on ws://0.0.0.0:8765")
            await server.wait_closed()
        except Exception as e:
            logger.error(f"WebSocket server error: {e}")

if __name__ == "__main__":
    # Configure logger
    logger.remove()
    logger.add(sys.stderr, level="INFO")
    logger.add("logs/lig_spread_recorder.log", rotation="10 MB", level="INFO")
    
    recorder = SpreadRecorder()
    try:
        asyncio.run(recorder.start())
    except KeyboardInterrupt:
        logger.info("Recorder stopped.")
