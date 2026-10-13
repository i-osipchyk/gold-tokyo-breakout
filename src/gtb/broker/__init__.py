"""cTrader Open API access (ARCHITECTURE §4.2)."""

from .client import (
    DEMO_HOST,
    LIVE_HOST,
    CTraderClient,
    CTraderError,
    Deal,
    PendingOrder,
    Position,
    SymbolSpec,
)

__all__ = [
    "DEMO_HOST", "LIVE_HOST", "CTraderClient", "CTraderError", "Deal", "PendingOrder", "Position",
    "SymbolSpec",
]
