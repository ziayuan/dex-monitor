"""Variational-Backpack spread strategy (placeholder for future implementation)."""
from .base import BaseStrategy, StrategyConfig


class VarBackpackStrategy(BaseStrategy):
    """Spread strategy between Variational and Backpack.
    
    TODO: Implement when Backpack integration is ready.
    """
    
    def __init__(self, config: StrategyConfig | None = None):
        super().__init__(config)
    
    @property
    def name(self) -> str:
        return "Var-Backpack"
    
    def calculate_signal(self, prices: dict) -> float:
        """Calculate spread with Backpack.
        
        TODO: Implement actual calculation when Backpack feed is available.
        """
        # Placeholder - will need backpack bid/ask
        return 0.0
