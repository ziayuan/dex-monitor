"""Variational-Paradex spread strategy."""
from .base import BaseStrategy, StrategyConfig


class VarParadexStrategy(BaseStrategy):
    """Spread strategy between Variational and Paradex.
    
    Two modes:
    - spread_a (Var卖): Var Bid - Para Ask (Sell Var, Buy Para)
    - spread_b (Par卖): Para Bid - Var Ask (Sell Para, Buy Var)
    """
    
    def __init__(self, mode: str = "spread_a", config: StrategyConfig | None = None):
        super().__init__(config)
        self.mode = mode  # "spread_a" or "spread_b"
    
    @property
    def name(self) -> str:
        if self.mode == "spread_a":
            return "Var卖-Para买"
        return "Para卖-Var买"
    
    def calculate_signal(self, prices: dict) -> float:
        """Calculate spread based on mode."""
        v_bid = prices.get("var", {}).get("bid", 0)
        v_ask = prices.get("var", {}).get("ask", 0)
        p_bid = prices.get("para", {}).get("bid", 0)
        p_ask = prices.get("para", {}).get("ask", 0)
        
        if self.mode == "spread_a":
            # Var卖-Para买: Var Bid - Para Ask
            return v_bid - p_ask if v_bid and p_ask else 0.0
        else:
            # Para卖-Var买: Para Bid - Var Ask
            return p_bid - v_ask if p_bid and v_ask else 0.0


class DualSpreadStrategy:
    """Manages two spread strategies simultaneously."""
    
    def __init__(self):
        self.strategy_a = VarParadexStrategy(mode="spread_a")
        self.strategy_b = VarParadexStrategy(mode="spread_b")
    
    def configure(
        self, 
        enable_a: bool = True, threshold_a: float = 5.0,
        enable_b: bool = False, threshold_b: float = 5.0,
        max_clicks: int = 10, cooldown: float = 5.0, confirm_count: int = 2
    ):
        """Configure both strategies."""
        self.strategy_a.config = StrategyConfig(
            enabled=enable_a, threshold=threshold_a,
            max_clicks=max_clicks, cooldown_seconds=cooldown, confirm_count=confirm_count
        )
        self.strategy_b.config = StrategyConfig(
            enabled=enable_b, threshold=threshold_b,
            max_clicks=max_clicks, cooldown_seconds=cooldown, confirm_count=confirm_count
        )
        # Share click counter
        self.strategy_b.clicks_performed = self.strategy_a.clicks_performed
    
    def start(self):
        self.strategy_a.start()
        self.strategy_b.start()
    
    def stop(self):
        self.strategy_a.stop()
        self.strategy_b.stop()
    
    @property
    def running(self) -> bool:
        return self.strategy_a.running or self.strategy_b.running
    
    def check(self, prices: dict) -> str | None:
        """Check both strategies.
        
        Returns:
            "a" if spread_a triggered, "b" if spread_b triggered, None otherwise
        """
        if self.strategy_a.check(prices) and self.strategy_a.can_execute():
            return "a"
        if self.strategy_b.check(prices) and self.strategy_b.can_execute():
            return "b"
        return None
    
    def on_executed(self, source: str):
        """Mark execution for the triggered strategy."""
        if source == "a":
            self.strategy_a.on_executed()
            self.strategy_b.clicks_performed = self.strategy_a.clicks_performed
        else:
            self.strategy_b.on_executed()
            self.strategy_a.clicks_performed = self.strategy_b.clicks_performed
    
    def get_signals(self, prices: dict) -> tuple[float, float]:
        """Get current signal values for both strategies."""
        return (
            self.strategy_a.calculate_signal(prices),
            self.strategy_b.calculate_signal(prices)
        )
    
    @property
    def clicks_performed(self) -> int:
        return self.strategy_a.clicks_performed
    
    @property
    def max_clicks(self) -> int:
        return self.strategy_a.config.max_clicks
