"""Data feeds for Variational and Paradex exchanges."""
import asyncio
import json
from typing import Callable

import websockets
from curl_cffi.requests import AsyncSession

from .config import Config, load_config

# Module-level state
_config: Config | None = None
_prices: dict = {}
_pairs: dict = {}
_current_symbol: str = "BTC-USD"
_symbol_changed: asyncio.Event | None = None
_on_price_update: Callable | None = None


def init(config: Config | None = None, default_symbol: str = "BTC-USD"):
    """Initialize the data feeds module."""
    global _config, _prices, _pairs, _current_symbol, _symbol_changed
    
    _config = config or load_config()
    _pairs = {p["symbol"]: p for p in _config.pairs}
    _current_symbol = default_symbol if default_symbol in _pairs else list(_pairs.keys())[0]
    _symbol_changed = asyncio.Event()
    
    # Initialize price storage
    _prices = {
        s: {
            "var": {"bid": 0.0, "ask": 0.0},
            "para": {"bid": 0.0, "ask": 0.0},
        }
        for s in _pairs
    }


def set_price_callback(callback: Callable):
    """Set callback to be called when prices update."""
    global _on_price_update
    _on_price_update = callback


def get_current_symbol() -> str:
    return _current_symbol


def set_current_symbol(symbol: str):
    global _current_symbol, _symbol_changed
    if symbol in _pairs:
        _current_symbol = symbol
        if _symbol_changed:
            _symbol_changed.set()


def get_prices(symbol: str | None = None) -> dict:
    """Get prices for a symbol or current symbol."""
    return _prices.get(symbol or _current_symbol, {})


def get_all_prices() -> dict:
    return _prices.copy()


# Expose module state for direct access
def PAIRS() -> dict:
    return _pairs


def DataStore() -> dict:
    return _prices


async def fetch_variational(session: AsyncSession, symbol: str | None = None) -> bool:
    """Fetch Variational quote for a single symbol."""
    global _prices
    
    sym = symbol or _current_symbol
    pair = _pairs.get(sym)
    if not pair:
        return False

    headers = dict(_config.var_headers)
    headers["cookie"] = _config.var_cookie
    payload = {
        "instrument": {
            "underlying": pair["underlying"],
            "funding_interval_s": 3600,
            "settlement_asset": "USDC",
            "instrument_type": "perpetual_future",
        },
        "qty": pair.get("qty", "0.01"),
    }
    try:
        proxies = None
        if _config.var_http_proxy:
            proxies = {"http": _config.var_http_proxy, "https": _config.var_http_proxy}
        
        resp = await session.post(
            _config.var_url,
            headers=headers,
            json=payload,
            proxies=proxies,
            impersonate="chrome120",
            timeout=5,
            verify=False,
        )
        if resp.status_code == 200:
            data = resp.json()
            _prices[sym]["var"]["bid"] = float(data.get("bid", 0))
            _prices[sym]["var"]["ask"] = float(data.get("ask", 0))
            if _on_price_update:
                _on_price_update()
            return True
    except Exception:
        pass
    return False


async def monitor_variational():
    """Continuously poll Variational for current symbol."""
    async with AsyncSession() as session:
        while True:
            await fetch_variational(session)
            await asyncio.sleep(_config.poll_interval_s)


async def monitor_paradex():
    """WebSocket connection to Paradex BBO feed."""
    global _prices
    
    backoff = _config.ws_backoff_start
    current_market = None

    while True:
        try:
            print(f"Connecting to Paradex WS...")
            async with websockets.connect(_config.paradex_ws_url, ping_interval=20, ping_timeout=20) as ws:
                pair = _pairs.get(_current_symbol)
                if pair:
                    market = pair["paradex_market"]
                    sub_msg = json.loads(
                        json.dumps(_config.paradex_subscribe_template).replace("{market}", market)
                    )
                    await ws.send(json.dumps(sub_msg))
                    current_market = market
                    print(f"Subscribed to {market}")

                async for message in ws:
                    # Handle symbol change
                    if _symbol_changed and _symbol_changed.is_set():
                        _symbol_changed.clear()
                        new_pair = _pairs.get(_current_symbol)
                        if new_pair:
                            new_market = new_pair["paradex_market"]
                            if new_market != current_market:
                                # Unsubscribe old
                                if current_market:
                                    unsub = json.loads(
                                        json.dumps(_config.paradex_unsubscribe_template).replace("{market}", current_market)
                                    )
                                    await ws.send(json.dumps(unsub))
                                    print(f"Unsubscribed {current_market}")
                                    _prices[_current_symbol]["para"] = {"bid": 0.0, "ask": 0.0}

                                # Subscribe new
                                sub = json.loads(
                                    json.dumps(_config.paradex_subscribe_template).replace("{market}", new_market)
                                )
                                await ws.send(json.dumps(sub))
                                current_market = new_market
                                print(f"Subscribed to {new_market}")

                    # Parse message
                    data = json.loads(message)
                    if data.get("method") == "subscription":
                        params = data.get("params", {})
                        payload = params.get("data", {})
                        channel = params.get("channel", "")
                        if "bbo." not in channel and "bbo:" not in channel:
                            continue
                    else:
                        payload = data.get("data", data)

                    market = payload.get("market") or payload.get("market_id") or payload.get("symbol")
                    if not market or market != current_market:
                        continue

                    # Find target symbol
                    target_sym = None
                    for s, p in _pairs.items():
                        if p["paradex_market"] == market:
                            target_sym = s
                            break

                    if target_sym:
                        bid = float(payload.get("bid", 0) or 0)
                        ask = float(payload.get("ask", 0) or 0)
                        if bid and ask:
                            _prices[target_sym]["para"]["bid"] = bid
                            _prices[target_sym]["para"]["ask"] = ask
                            if _on_price_update:
                                _on_price_update()

                backoff = _config.ws_backoff_start
        except Exception as e:
            print(f"Para WS Error: {e}, Reconnecting...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, _config.ws_backoff_max)


def calculate_spreads(symbol: str | None = None) -> tuple[float, float]:
    """Calculate spreads for a symbol.
    
    Returns:
        (spread_a, spread_b) where:
        - spread_a = Var Bid - Para Ask (Var卖-Para买)
        - spread_b = Para Bid - Var Ask (Para卖-Var买)
    """
    sym = symbol or _current_symbol
    data = _prices.get(sym)
    if not data:
        return 0.0, 0.0
    
    v_bid, v_ask = data["var"]["bid"], data["var"]["ask"]
    p_bid, p_ask = data["para"]["bid"], data["para"]["ask"]
    
    spread_a = v_bid - p_ask if v_bid and p_ask else 0.0
    spread_b = p_bid - v_ask if p_bid and v_ask else 0.0
    
    return spread_a, spread_b
