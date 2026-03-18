
import asyncio
import json
from typing import Optional, Callable
from loguru import logger

# Import official SDK for API (market lookup)
# Import official SDK removed for security reasons
# using direct http calls via curl_cffi and websockets instead

import websockets

from .lighter_ws import LighterMonitor

class LighterClient:
    """Lighter exchange client for market data (no private key needed)."""
    
    # Fallback market map (from user provided data)
    FALLBACK_MARKETS = {
        "BTC": 1,
        "SOL": 2,
        "ETH": 0,
        "DOGE": 3,
        "WIF": 5,
        "WLD": 6,
        "XRP": 7,
        "LINK": 8,
        "AVAX": 9,
        "NEAR": 10,
        "DOT": 11,
        "TON": 12,
        "TAO": 13,
        "SUI": 16,
        "APE": 26, # JUP actually, need to be careful with mismatched names, checking logic below
        # Correcting based on user list provided:
        # "ETH" -> 0
        # "BTC" -> 1
        # "SOL" -> 2
        # "DOGE" -> 3
        # "WIF" -> 5
        # "WLD" -> 6
        # "XRP" -> 7
        # "LINK" -> 8
        # "AVAX" -> 9
        # "NEAR" -> 10
        # "DOT" -> 11
        # "TON" -> 12
        # "TAO" -> 13
    }
    
    
    
    def __init__(self, public_key: str = None, private_key: str = None, api_index: int = 0, l1_address: str = None):
        self.base_url = "https://mainnet.zklighter.elliot.ai"
        self.ws_url = "wss://mainnet.zklighter.elliot.ai/stream"
        self.public_key = public_key
        self.private_key = private_key
        self.api_index = api_index
        self.l1_address = l1_address
        self._dynamic_markets = {}  # {symbol: market_id} fetched from API
        
        # Removed unsupported lighter SDK to prevent supply chain risks
        self.api_client = None

    async def get_funding_rates(self) -> dict:
        """Fetch funding rates from Lighter REST API.

        Returns:
            {symbol: {"lighter": rate, "hyperliquid": rate, "binance": rate, "bybit": rate}}
            Rates are per-funding-period (hourly for lighter/hl, varies for cex).
        """
        url = f"{self.base_url}/api/v1/funding-rates"
        try:
            from curl_cffi.requests import AsyncSession

            async with AsyncSession() as session:
                resp = await session.get(
                    url,
                    headers={"accept": "application/json"},
                    impersonate="chrome116",
                    timeout=15,
                )

                if resp.status_code == 200:
                    data = resp.json()
                    rates = {}
                    for item in data.get("funding_rates", []):
                        symbol = item.get("symbol")
                        exchange = item.get("exchange", "unknown")
                        rate = item.get("rate", 0)
                        if symbol:
                            if symbol not in rates:
                                rates[symbol] = {}
                            rates[symbol][exchange] = rate
                    logger.info(f"Fetched Lighter funding rates for {len(rates)} symbols")
                    return rates
                else:
                    logger.error(f"Lighter funding-rates API error: {resp.status_code}")

        except ImportError:
            logger.error("curl_cffi not installed")
        except Exception as e:
            logger.error(f"Error fetching Lighter funding rates: {e}")

        return {}

    async def get_market_prices(self) -> dict:
        """Fetch mark/index prices for all markets via WS market_stats.

        Returns:
            {symbol: {"mark": float, "index": float}}
        """
        try:
            async with websockets.connect(self.ws_url) as ws:
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "channel": "market_stats/all"
                }))

                for _ in range(3):
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(msg)

                    if data.get("type") == "connected":
                        continue

                    stats = data.get("market_stats", {})
                    if stats:
                        prices = {}
                        for k, v in stats.items():
                            sym = v.get("symbol")
                            mark = v.get("mark_price")
                            index = v.get("index_price")
                            if sym and mark:
                                prices[sym] = {
                                    "mark": float(mark),
                                    "index": float(index) if index else None,
                                }
                        logger.info(f"Fetched {len(prices)} Lighter market prices")
                        return prices

        except Exception as e:
            logger.error(f"Failed to fetch Lighter market prices: {e}")

        return {}

    async def get_positions(self) -> dict:
        """Fetch positions via Public API (using L1 Address). Returns dict {symbol: qty}."""
        if not self.l1_address:
             logger.warning("No L1 address provided for public API position fetch")
             return {}
        
        url = f"{self.base_url}/api/v1/account?by=l1_address&value={self.l1_address}"
        try:
             from curl_cffi.requests import AsyncSession
             async with AsyncSession() as session:
                 resp = await session.get(url, headers={"accept": "application/json"}, impersonate="chrome116")
                 
                 if resp.status_code == 200:
                     data = resp.json()
                     # Structure: { accounts: [ { positions: [ { symbol: "ETH", position: "0.1", ... } ] } ] }
                     if "accounts" in data and len(data["accounts"]) > 0:
                         acct = data["accounts"][0]
                         positions = {}
                         for p in acct.get("positions", []):
                             symbol = p.get("symbol")
                             amt = float(p.get("position", 0))
                             sign = int(p.get("sign", 1))
                             
                             # Apply sign (sign -1 means short)
                             amt = amt * sign
                             
                             # Only track non-zero positions to avoid clutter
                             if symbol and abs(amt) > 0:
                                 positions[symbol] = amt
                         return positions
                     else:
                         logger.warning("No account data in Lighter response")
                 else:
                     logger.error(f"Lighter API error: {resp.status_code} {resp.text}")
                     
        except ImportError:
            logger.error("curl_cffi not installed, cannot fetch public API")
        except Exception as e:
            logger.error(f"Error fetching Lighter positions: {e}")
            
        return {}

    async def fetch_markets(self) -> dict:
        """Fetch all markets from Lighter via WS market_stats/all channel.
        
        Returns {symbol: market_id} dict and caches in _dynamic_markets.
        """
        try:
            async with websockets.connect(self.ws_url) as ws:
                await ws.send(json.dumps({
                    "type": "subscribe",
                    "channel": "market_stats/all"
                }))
                
                for _ in range(3):
                    msg = await asyncio.wait_for(ws.recv(), timeout=5)
                    data = json.loads(msg)
                    
                    if data.get("type") == "connected":
                        continue
                    
                    stats = data.get("market_stats", {})
                    if stats:
                        markets = {}
                        for k, v in stats.items():
                            sym = v.get("symbol")
                            mid = v.get("market_id")
                            if sym and mid is not None:
                                markets[sym] = int(mid)
                        self._dynamic_markets = markets
                        logger.info(f"Fetched {len(markets)} Lighter markets")
                        return markets
                
        except Exception as e:
            logger.error(f"Failed to fetch Lighter markets: {e}")
        
        return {}

    async def get_market_id(self, symbol: str) -> Optional[int]:
        """Get market ID for a symbol (e.g. 'BTC-USD' or 'AAVE')."""
        
        # strip -USD if present for lookup
        lookup_sym = symbol.replace("-USD", "")
        
        # Try dynamic markets first (fetched from API)
        if lookup_sym in self._dynamic_markets:
            logger.info(f"Using dynamic ID for {lookup_sym}: {self._dynamic_markets[lookup_sym]}")
            return self._dynamic_markets[lookup_sym]
        
        # Fallback to hardcoded
        if lookup_sym in self.FALLBACK_MARKETS:
             logger.info(f"Using fallback ID for {lookup_sym}: {self.FALLBACK_MARKETS[lookup_sym]}")
             return self.FALLBACK_MARKETS[lookup_sym]

        logger.warning(f"Symbol {symbol} (lookup: {lookup_sym}) not found in any market map")
        return None

    def create_monitor(self, market_id: int, on_price_update: Optional[Callable] = None) -> LighterMonitor:
        """Create a WebSocket monitor for the given market."""
        return LighterMonitor(market_id, on_price_update)

    async def close(self):
        pass
