from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Bar:
    """A bid trendbar. ``ts`` is the bar's OPEN time, tz-aware UTC."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
