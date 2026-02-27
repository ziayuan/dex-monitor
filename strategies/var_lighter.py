"""Variational-Lighter Hedge Strategy (Click Only)."""
import asyncio
import time
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Callable, Optional

from loguru import logger


class HedgeDirection(Enum):
    """Hedge direction."""
    VAR_SELL = "var_sell"   # Sell on Var, Buy on Lig
    LIG_SELL = "lig_sell"   # Sell on Lig, Buy on Var


@dataclass
class StrategyConfig:
    """Strategy configuration."""
    direction: HedgeDirection = HedgeDirection.VAR_SELL
    threshold: Decimal = Decimal("5.0")
    cooldown_seconds: float = 3.0
    max_trades: int = 100

@dataclass
class StrategyState:
    """Mutable strategy state."""
    trades_executed: int = 0
    last_trade_time: float = 0
    is_cooling_down: bool = False

@dataclass 
class PriceData:
    """Price data for spread calculation."""
    lig_bid: Decimal = Decimal(0)
    lig_ask: Decimal = Decimal(0)
    var_bid: Decimal = Decimal(0)
    var_ask: Decimal = Decimal(0)
    
    def spread_var_sell(self) -> Decimal:
        """Spread for Var Sell (Buy Lighter).
        Profit = Var_Bid - Lig_Ask
        """
        if self.var_bid and self.lig_ask:
            return self.var_bid - self.lig_ask
        return Decimal(0)
    
    def spread_lig_sell(self) -> Decimal:
        """Spread for Lig Sell (Buy Var).
        Profit = Lig_Bid - Var_Ask
        """
        if self.lig_bid and self.var_ask:
            return self.lig_bid - self.var_ask
        return Decimal(0)


class VarLighterStrategy:
    """
    Lighter-Variational hedge strategy (Taker-Taker).
    
    Flow:
    1. Monitor spread between Lig and Var
    2. When spread >= threshold, click checks on both platforms
    """
    
    def __init__(
        self,
        click_callback: Callable,
        config: StrategyConfig | None = None
    ):
        self.click_callback = click_callback
        self.config = config or StrategyConfig()
        self.state = StrategyState()
        
        self.running = False
        self.symbol = ""
        self.prices = PriceData()
        
        # Callbacks
        self._on_trade: Optional[Callable] = None
    
    def set_callbacks(self, on_trade: Callable):
        self._on_trade = on_trade
    
    def configure(
        self,
        direction: HedgeDirection,
        threshold: Decimal,
        cooldown: float = 3.0,
        max_trades: int = 100
    ):
        self.config.direction = direction
        self.config.threshold = threshold
        self.config.cooldown_seconds = cooldown
        self.config.max_trades = max_trades
    
    # ========== Price Updates ==========
    
    def update_lig_prices(self, bid: Decimal, ask: Decimal):
        """Update Lighter BBO."""
        self.prices.lig_bid = bid
        self.prices.lig_ask = ask
        if self.running:
            self._check_strategy()
    
    def update_var_prices(self, bid: Decimal, ask: Decimal):
        """Update Variational BBO."""
        self.prices.var_bid = bid
        self.prices.var_ask = ask
        if self.running:
            self._check_strategy()
    
    def get_current_spread(self) -> Decimal:
        if self.config.direction == HedgeDirection.VAR_SELL:
            return self.prices.spread_var_sell()
        return self.prices.spread_lig_sell()
    
    # ========== Strategy Logic ==========
    
    def _check_strategy(self):
        """Check if conditions met for click."""
        if not self.running:
            return
            
        # Calculate spread first to see if we SHOULD trade
        spread = self.get_current_spread()
        
        if spread >= self.config.threshold:
            # Check constraints and log if blocked
            if self.state.trades_executed >= self.config.max_trades:
                # Log only once to avoid spam? Or maybe just warning
                # We can check if we just logged this to avoid spamming
                pass 
                # Actually, better to just return, user sees status in UI. 
                # Or log once. 
                return

            # Cooldown check
            now = time.time()
            if now - self.state.last_trade_time < self.config.cooldown_seconds:
                # logger.debug(f"Spread {spread} >= Threshold, but Cooldown active")
                return
                
            self._execute_hedge(spread)
    
    def _execute_hedge(self, spread: Decimal):
        """Execute dual clicks."""
        logger.info(f"⚡️ Spread {spread:.2f} >= {self.config.threshold} -> EXECUTING CLICKS!")
        
        try:
            # Execute clicks (using blocking call since we want immediate action)
            # click_callback points to core.clicker.perform_clicks
            self.click_callback()
            
            self.state.trades_executed += 1
            self.state.last_trade_time = time.time()
            
            # Simple PnL calc (slippage ignored)
            if self._on_trade:
                trade_data = {
                    "trade_num": self.state.trades_executed,
                    "spread": spread,
                    "timestamp": self.state.last_trade_time
                }
                self._on_trade(trade_data)
                
        except Exception as e:
            logger.error(f"Click execution failed: {e}")

    # ========== Lifecycle ==========
    
    def start(self, symbol: str):
        self.symbol = symbol
        self.running = True
        logger.info(f"🚀 Strategy started: {symbol}")
    
    def stop(self):
        self.running = False
        logger.info("🛑 Strategy stopped")
    
    def reset(self):
        self.state = StrategyState()
