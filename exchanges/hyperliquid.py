"""TradeXYZ (Hyperliquid HIP-3) exchange client for funding rate data."""

import asyncio
from typing import Optional
from loguru import logger


class HyperliquidClient:
    """Client to fetch TradeXYZ (HIP-3 dex 'xyz') funding rate data from Hyperliquid API."""

    BASE_URL = "https://api.hyperliquid.xyz/info"
    DEX_NAME = "xyz"  # TradeXYZ HIP-3 dex name

    def __init__(self, proxy: str = "http://127.0.0.1:7897"):
        self.proxy = proxy

    async def get_funding_rates(self) -> dict:
        """Fetch current funding rates for all TradeXYZ (HIP-3) markets.

        Returns:
            {symbol: {"funding": float, "mark_px": str, "oracle_px": str, "premium": str, "oi": str}}
        """
        try:
            from curl_cffi.requests import AsyncSession

            async with AsyncSession() as session:
                resp = await session.post(
                    self.BASE_URL,
                    json={"type": "metaAndAssetCtxs", "dex": self.DEX_NAME},
                    headers={"Content-Type": "application/json"},
                    impersonate="chrome116",
                    proxies={"https": self.proxy} if self.proxy else None,
                    timeout=15,
                )

                if resp.status_code != 200:
                    logger.error(f"Hyperliquid API error: {resp.status_code}")
                    return {}

                data = resp.json()
                if not data or not isinstance(data, list) or len(data) < 2:
                    logger.warning(f"Unexpected Hyperliquid response: {str(data)[:200]}")
                    return {}

                universe = data[0]["universe"]
                ctxs = data[1]

                rates = {}
                for asset_info, ctx in zip(universe, ctxs):
                    symbol = asset_info["name"]
                    # Strip HIP-3 dex prefix (e.g. "xyz:EUR" -> "EUR")
                    if ":" in symbol:
                        symbol = symbol.split(":", 1)[1]
                    rates[symbol] = {
                        "funding": float(ctx.get("funding", 0)),
                        "mark_px": ctx.get("markPx", "0"),
                        "oracle_px": ctx.get("oraclePx", "0"),
                        "premium": ctx.get("premium", "0"),
                        "oi": ctx.get("openInterest", "0"),
                    }

                logger.info(f"Fetched {len(rates)} TradeXYZ funding rates")
                return rates

        except ImportError:
            logger.error("curl_cffi not installed")
        except Exception as e:
            logger.error(f"Error fetching TradeXYZ funding rates: {e}")

        return {}

    async def get_funding_rate(self, symbol: str) -> Optional[float]:
        """Get funding rate for a single symbol on TradeXYZ.

        Args:
            symbol: Asset symbol (e.g. 'EUR', 'JPY', 'BTC')

        Returns:
            Hourly funding rate as float, or None if not found
        """
        rates = await self.get_funding_rates()
        if symbol in rates:
            return rates[symbol]["funding"]
        return None
