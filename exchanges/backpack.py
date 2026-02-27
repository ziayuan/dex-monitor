"""Backpack Exchange Client with WebSocket support for BBO and Order Updates."""
import asyncio
import base64
import json
import os
import time
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

import websockets
from cryptography.hazmat.primitives.asymmetric import ed25519
from dotenv import load_dotenv
from loguru import logger

# Load environment variables
load_dotenv()


class OrderSide(Enum):
    BUY = "Bid"
    SELL = "Ask"


class OrderStatus(Enum):
    NEW = "new"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELED = "canceled"


@dataclass
class OrderResult:
    success: bool
    order_id: str = ""
    side: str = ""
    price: Decimal = Decimal(0)
    quantity: Decimal = Decimal(0)
    status: str = ""
    error_message: str = ""
    filled_qty: Decimal = Decimal(0)


@dataclass
class BBO:
    bid: Decimal = Decimal(0)
    ask: Decimal = Decimal(0)
    bid_qty: Decimal = Decimal(0)
    ask_qty: Decimal = Decimal(0)
    timestamp: float = 0


class BackpackClient:
    """Backpack exchange client with WebSocket for BBO and order updates."""
    
    WS_URL = "wss://ws.backpack.exchange"
    
    def __init__(self):
        self.public_key = os.getenv("BACKPACK_PUBLIC_KEY", "")
        self.secret_key = os.getenv("BACKPACK_SECRET_KEY", "")
        
        if not self.public_key or not self.secret_key:
            raise ValueError("BACKPACK_PUBLIC_KEY and BACKPACK_SECRET_KEY must be set in .env")
        
        # Initialize ED25519 private key
        self._private_key = ed25519.Ed25519PrivateKey.from_private_bytes(
            base64.b64decode(self.secret_key)
        )
        
        # SDK client for REST API
        try:
            from bpx.public import Public
            from bpx.account import Account
            self._public = Public()
            self._account = Account(public_key=self.public_key, secret_key=self.secret_key)
        except ImportError:
            logger.warning("bpx SDK not installed, REST API unavailable")
            self._public = None
            self._account = None
        
        # WebSocket state
        self._bbo_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._order_ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        
        # Callbacks
        self._on_bbo_update: Optional[Callable[[str, BBO], None]] = None
        self._on_order_update: Optional[Callable[[dict], None]] = None
        
        # Current BBO cache
        self._bbo_cache: dict[str, BBO] = {}
        
        # Current symbol
        self.symbol = ""
    
    def _sign(self, instruction: str, timestamp: int, window: int = 5000) -> str:
        """Generate ED25519 signature for WebSocket authentication."""
        message = f"instruction={instruction}&timestamp={timestamp}&window={window}"
        signature_bytes = self._private_key.sign(message.encode())
        return base64.b64encode(signature_bytes).decode()
    
    # ========== Public WebSocket (BBO via bookTicker) ==========
    
    async def subscribe_bbo(self, symbol: str, callback: Callable[[str, BBO], None]):
        """Subscribe to BBO updates via bookTicker stream."""
        self.symbol = symbol
        self._on_bbo_update = callback
        self._running = True
        
        while self._running:
            try:
                logger.info(f"Connecting to Backpack bookTicker WebSocket for {symbol}...")
                async with websockets.connect(self.WS_URL, ping_interval=20) as ws:
                    self._bbo_ws = ws
                    
                    # Subscribe to bookTicker (best bid/ask)
                    sub_msg = {
                        "method": "SUBSCRIBE",
                        "params": [f"bookTicker.{symbol}"]
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info(f"Subscribed to bookTicker.{symbol}")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_bbo_message(message)
                        
            except websockets.exceptions.ConnectionClosed:
                logger.warning("BBO WebSocket closed, reconnecting in 2s...")
            except Exception as e:
                logger.error(f"BBO WebSocket error: {e}")
            
            if self._running:
                await asyncio.sleep(2)
    
    async def _handle_bbo_message(self, message: str):
        """Parse bookTicker message and call callback."""
        try:
            data = json.loads(message)
            stream = data.get("stream", "")
            
            if "bookTicker" not in stream:
                return
            
            payload = data.get("data", {})
            
            # bookTicker format:
            # "a": Inside ask price, "A": Inside ask quantity
            # "b": Inside bid price, "B": Inside bid quantity
            bid_price = payload.get("b", "0")
            ask_price = payload.get("a", "0")
            bid_qty = payload.get("B", "0")
            ask_qty = payload.get("A", "0")
            
            if not bid_price or not ask_price:
                return
            
            bbo = BBO(
                bid=Decimal(bid_price),
                ask=Decimal(ask_price),
                bid_qty=Decimal(bid_qty),
                ask_qty=Decimal(ask_qty),
                timestamp=time.time()
            )
            
            symbol = payload.get("s", stream.replace("bookTicker.", ""))
            self._bbo_cache[symbol] = bbo
            
            if self._on_bbo_update:
                self._on_bbo_update(symbol, bbo)
                
        except Exception as e:
            logger.error(f"Error parsing bookTicker message: {e}")
    
    def get_cached_bbo(self, symbol: str) -> Optional[BBO]:
        """Get cached BBO for a symbol."""
        return self._bbo_cache.get(symbol)
    
    # ========== Private WebSocket (Order Updates) ==========
    
    async def subscribe_order_updates(self, symbol: str, callback: Callable[[dict], None]):
        """Subscribe to order updates (private, requires auth)."""
        self._on_order_update = callback
        self._running = True
        
        while self._running:
            try:
                logger.info(f"Connecting to Backpack Order WebSocket for {symbol}...")
                async with websockets.connect(self.WS_URL, ping_interval=20) as ws:
                    self._order_ws = ws
                    
                    # Subscribe with authentication
                    timestamp = int(time.time() * 1000)
                    signature = self._sign("subscribe", timestamp)
                    
                    sub_msg = {
                        "method": "SUBSCRIBE",
                        "params": [f"account.orderUpdate.{symbol}"],
                        "signature": [
                            self.public_key,
                            signature,
                            str(timestamp),
                            "5000"
                        ]
                    }
                    await ws.send(json.dumps(sub_msg))
                    logger.info(f"Subscribed to order updates for {symbol}")
                    
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_order_message(message)
                        
            except websockets.exceptions.ConnectionClosed:
                logger.warning("Order WebSocket closed, reconnecting in 2s...")
            except Exception as e:
                logger.error(f"Order WebSocket error: {e}")
            
            if self._running:
                await asyncio.sleep(2)
    
    async def _handle_order_message(self, message: str):
        """Parse order update message."""
        try:
            data = json.loads(message)
            stream = data.get("stream", "")
            
            if "orderUpdate" not in stream:
                return
            
            payload = data.get("data", {})
            
            # Parse order data
            event_type = payload.get("e", "")  # orderFill, orderAccepted, orderCancelled
            order_id = payload.get("i", "")
            symbol = payload.get("s", "")
            side = payload.get("S", "")  # BID or ASK
            quantity = Decimal(payload.get("q", "0"))
            price = Decimal(payload.get("p", "0"))
            filled_qty = Decimal(payload.get("z", "0"))
            
            order_data = {
                "event": event_type,
                "order_id": order_id,
                "symbol": symbol,
                "side": "buy" if side.upper() == "BID" else "sell",
                "quantity": quantity,
                "price": price,
                "filled_qty": filled_qty,
                # Only fully filled - check filled quantity equals order quantity
                "is_fully_filled": event_type == "orderFill" and filled_qty >= quantity
            }
            
            logger.info(f"📋 Order update: {event_type} id={order_id} {side} filled={filled_qty}/{quantity} @ {price}")
            
            if self._on_order_update:
                self._on_order_update(order_data)
                
        except Exception as e:
            logger.error(f"Error parsing order message: {e}")
    
    # ========== REST API (Orders) ==========
    
    async def place_limit_order(
        self, 
        symbol: str, 
        side: OrderSide, 
        price: Decimal, 
        quantity: Decimal,
        post_only: bool = True
    ) -> OrderResult:
        """Place a limit order (Maker)."""
        if not self._account:
            return OrderResult(success=False, error_message="bpx SDK not available")
        
        try:
            from bpx.constants.enums import OrderTypeEnum, TimeInForceEnum
            
            result = self._account.execute_order(
                symbol=symbol,
                side=side.value,
                order_type=OrderTypeEnum.LIMIT,
                quantity=str(quantity),
                price=str(price),
                post_only=post_only,
                time_in_force=TimeInForceEnum.GTC
            )
            
            if not result:
                return OrderResult(success=False, error_message="No response from API")
            
            if "code" in result:
                return OrderResult(
                    success=False, 
                    error_message=result.get("message", "Unknown error")
                )
            
            order_id = result.get("id", "")
            if not order_id:
                return OrderResult(success=False, error_message="No order ID in response")
            
            logger.info(f"Order placed: {order_id} {side.value} {quantity} @ {price}")
            
            return OrderResult(
                success=True,
                order_id=order_id,
                side=side.value.lower(),
                price=price,
                quantity=quantity,
                status="new"
            )
            
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            return OrderResult(success=False, error_message=str(e))
    
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """Cancel an order."""
        if not self._account:
            return False
        
        try:
            result = self._account.cancel_order(symbol=symbol, order_id=order_id)
            
            if result and "code" not in result:
                logger.info(f"Order cancelled: {order_id}")
                return True
            
            logger.warning(f"Failed to cancel order {order_id}: {result}")
            return False
            
        except Exception as e:
            logger.error(f"Error cancelling order: {e}")
            return False
    
    async def cancel_all_orders(self, symbol: str) -> bool:
        """Cancel all orders for a symbol."""
        if not self._account:
            return False
        
        try:
            result = self._account.cancel_all_orders(symbol=symbol)
            logger.info(f"Cancelled all orders for {symbol}")
            return True
        except Exception as e:
            logger.error(f"Error cancelling all orders: {e}")
            return False
    
    # ========== Lifecycle ==========
    
    async def connect(self, symbol: str, on_bbo: Callable, on_order: Callable):
        """Start both WebSocket connections."""
        self.symbol = symbol
        self._on_bbo_update = on_bbo
        self._on_order_update = on_order
        self._running = True
        
        await asyncio.gather(
            self.subscribe_bbo(symbol, on_bbo),
            self.subscribe_order_updates(symbol, on_order)
        )
    
    async def disconnect(self):
        """Stop all WebSocket connections."""
        self._running = False
        
        if self._bbo_ws:
            await self._bbo_ws.close()
        if self._order_ws:
            await self._order_ws.close()
        
        logger.info("Backpack client disconnected")
