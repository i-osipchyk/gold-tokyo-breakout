"""ATR(4h, 20) the way the reference backtest computed it (ARCHITECTURE §2): Wilder's ATR over
4h bars rebuilt from 1h bars, buckets anchored to 17:00 America/New_York, sealed bars only."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .bar import Bar

NY = ZoneInfo("America/New_York")
PERIOD = 20


def _bucket_start(ts: datetime) -> datetime:
    """Open of the 4h bucket holding ``ts``: 17, 21, 01, 05, 09 or 13 New York wall time."""
    wall = ts.astimezone(NY).replace(tzinfo=None, minute=0, second=0, microsecond=0)
    return (wall - timedelta(hours=(wall.hour - 1) % 4)).replace(tzinfo=NY)


def _bucket_end(start_local: datetime) -> datetime:
    return (start_local.replace(tzinfo=None) + timedelta(hours=4)).replace(tzinfo=NY)


def sealed_4h(bars_1h: list[Bar], at: datetime) -> list[Bar]:
    """4h bars from the 1h bars opened before ``at`` whose bucket has ended by ``at``."""
    out: list[Bar] = []
    current: Bar | None = None                # ts = bucket open (New York time)
    for b in sorted((b for b in bars_1h if b.ts < at), key=lambda b: b.ts):
        start = _bucket_start(b.ts)
        if current is not None and current.ts == start:
            current = Bar(start, current.open, max(current.high, b.high),
                          min(current.low, b.low), b.close)
            continue
        if current is not None:
            out.append(current)
        current = Bar(start, b.open, b.high, b.low, b.close)
    if current is not None and _bucket_end(current.ts) <= at:
        out.append(current)
    return out


def wilder_atr(bars: list[Bar], period: int = PERIOD) -> float | None:
    """Seeded with the mean of the first ``period`` true ranges (the first bar has none)."""
    trs = [max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
           for p, c in zip(bars, bars[1:], strict=False)]
    if len(trs) < period:
        return None
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr
