"""
Custom Lighter WebSocket implementation for Lighter-Var Hedge Bot.
Adapted from reference code.
"""

import asyncio
import json
import time
from typing import Dict, Any, List, Optional, Tuple, Callable
import websockets
from loguru import logger

class LighterMonitor:
    """Monitor Lighter BBO via WebSocket (Public only)."""

    def __init__(self, market_index: int, on_price_update: Optional[Callable] = None):
        self.market_index = market_index
        self.on_price_update = on_price_update
        self.running = False
        self.ws = None

        # Order book state
        self.order_book = {"bids": {}, "asks": {}}
        self.best_bid = 0.0
        self.best_ask = 0.0
        self.snapshot_loaded = False
        self.order_book_offset = None
        self.order_book_sequence_gap = False
        self.order_book_lock = asyncio.Lock()

        # WebSocket URL
        self.ws_url = "wss://mainnet.zklighter.elliot.ai/stream"

    # Removed duplicate _log methods
    # Using loguru logger directly in methods


    def update_order_book(self, side: str, updates: List[Dict[str, Any]]):
        """Update the order book with new price/size information."""
        if side not in ["bids", "asks"]:
            return

        ob = self.order_book[side]

        for update in updates:
            try:
                price = float(update.get("price", 0))
                size = float(update.get("size", 0))

                if size == 0:
                    ob.pop(price, None)
                else:
                    ob[price] = size
            except (ValueError, TypeError):
                continue

    def get_best_levels(self) -> Tuple[Tuple[Optional[float], Optional[float]], Tuple[Optional[float], Optional[float]]]:
        """Get the best bid and ask levels with sufficient size."""
        try:
            # Simple BBO - get top levels
            if not self.order_book["bids"]:
                best_bid = (None, None)
            else:
                price = max(self.order_book["bids"].keys())
                best_bid = (price, self.order_book["bids"][price])

            if not self.order_book["asks"]:
                best_ask = (None, None)
            else:
                price = min(self.order_book["asks"].keys())
                best_ask = (price, self.order_book["asks"][price])

            return best_bid, best_ask
        except Exception:
            return (None, None), (None, None)

    async def connect(self):
        """Connect to Lighter WebSocket."""
        self.running = True
        while self.running:  # ← exit loop when disconnect() is called
            try:
                # Reset state
                self.order_book["bids"].clear()
                self.order_book["asks"].clear()
                self.snapshot_loaded = False
                
                logger.info(f"Connecting to Lighter WS for market {self.market_index}...")

                async with websockets.connect(self.ws_url) as self.ws:
                    # Subscribe to order book updates
                    await self.ws.send(json.dumps({
                        "type": "subscribe",
                        "channel": f"order_book/{self.market_index}"
                    }))

                    logger.info("Connected to Lighter WS")

                    while self.running:
                        try:
                            msg = await asyncio.wait_for(self.ws.recv(), timeout=5)
                            data = json.loads(msg)
                            
                            msg_type = data.get("type")

                            async with self.order_book_lock:
                                if msg_type == "subscribed/order_book":
                                    # Snapshot
                                    ob = data.get("order_book", {})
                                    self.order_book["bids"].clear()
                                    self.order_book["asks"].clear()
                                    self.order_book_offset = ob.get("offset")
                                    
                                    self.update_order_book("bids", ob.get("bids", []))
                                    self.update_order_book("asks", ob.get("asks", []))
                                    self.snapshot_loaded = True
                                    logger.info("Lighter snapshot loaded")

                                elif msg_type == "update/order_book" and self.snapshot_loaded:
                                    ob = data.get("order_book", {})
                                    
                                    # Update
                                    self.update_order_book("bids", ob.get("bids", []))
                                    self.update_order_book("asks", ob.get("asks", []))
                                    
                                    # Update global BBO
                                    (bid_p, _), (ask_p, _) = self.get_best_levels()
                                    if bid_p is not None: self.best_bid = bid_p
                                    if ask_p is not None: self.best_ask = ask_p
                                    
                                    # Callback
                                    if self.on_price_update and bid_p and ask_p:
                                        self.on_price_update(self.best_bid, self.best_ask)

                                elif msg_type == "ping":
                                    await self.ws.send(json.dumps({"type": "pong"}))

                        except asyncio.TimeoutError:
                            continue
                            
            except Exception as e:
                if not self.running:
                    break  # intentional disconnect, don't log error
                logger.error(f"Lighter WS error: {e}")
                
            # Reconnect delay — only if still running
            if self.running:
                await asyncio.sleep(2)

    async def disconnect(self):
        """Disconnect and stop reconnecting."""
        self.running = False
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None
