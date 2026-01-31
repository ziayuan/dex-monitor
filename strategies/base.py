"""Base strategy interface for trading strategies."""
from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class StrategyConfig:
    """Common strategy configuration."""
    enabled: bool = False
    threshold: float = 5.0
    max_clicks: int = 10
    cooldown_seconds: float = 5.0
    confirm_count: int = 2  # Consecutive confirmations required


class BaseStrategy(ABC):
    """Abstract base class for trading strategies."""
    
    def __init__(self, config: StrategyConfig | None = None):
        self.config = config or StrategyConfig()
        self.consecutive_count = 0
        self.clicks_performed = 0
        self.last_trigger_time = 0
        self.running = False
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable strategy name."""
        pass
    
    @abstractmethod
    def calculate_signal(self, prices: dict) -> float:
        """Calculate the signal value from current prices.
        
        Args:
            prices: Dict with 'var' and 'para' bid/ask prices
            
        Returns:
            Signal value (e.g., spread)
        """
        pass
    
    def check(self, prices: dict) -> bool:
        """Check if strategy should trigger.
        
        Returns True if signal exceeds threshold for required consecutive times.
        """
        if not self.config.enabled or not self.running:
            return False
        
        signal = self.calculate_signal(prices)
        
        if signal >= self.config.threshold:
            self.consecutive_count += 1
            if self.consecutive_count >= self.config.confirm_count:
                return True
        else:
            self.consecutive_count = 0
        
        return False
    
    def can_execute(self) -> bool:
        """Check if execution is allowed (cooldown and max clicks)."""
        import time
        
        if self.clicks_performed >= self.config.max_clicks:
            return False
        
        if time.time() - self.last_trigger_time < self.config.cooldown_seconds:
            return False
        
        return True
    
    def on_executed(self):
        """Called after successful execution."""
        import time
        self.clicks_performed += 1
        self.last_trigger_time = time.time()
        self.consecutive_count = 0
    
    def start(self):
        """Start the strategy."""
        self.running = True
        self.clicks_performed = 0
        self.consecutive_count = 0
    
    def stop(self):
        """Stop the strategy."""
        self.running = False
    
    def reset(self):
        """Reset all counters."""
        self.clicks_performed = 0
        self.consecutive_count = 0
        self.last_trigger_time = 0
