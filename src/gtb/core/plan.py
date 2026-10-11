"""plan_day: the whole trading decision for one day (ARCHITECTURE §2).

Pure: no clock, I/O or env."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, time, timedelta
from statistics import median

from .atr import sealed_4h, wilder_atr
from .bar import Bar

TOKYO_END = time(9, 0)            # Tokyo window: 00:00 <= bar open < 09:00 UTC
MIN_RANGE_BARS = 50
LOOKBACK = 20
MIN_LOOKBACK = 15
TRIG_ATR = 0.02                   # entry offset beyond the range edge
SL_BUFFER_ATR = 0.05              # a shortened stop stays this far inside the opposite entry
EXPIRY = time(20, 5)              # last fill on the 20:00 bar
FLATTEN = time(20, 50)            # flatten fills at the 20:50 open
OZ_PER_LOT = 100
LOT_STEP = 0.01
TICK = 0.01


@dataclass(frozen=True)
class OrderPlan:
    side: str                     # "buy" | "sell"
    entry: float                  # stop trigger price
    stop_loss: float
    lots: float
    expiry: datetime              # no fills from this time on


@dataclass(frozen=True)
class DayPlan:
    day: date
    tokyo_high: float | None
    tokyo_low: float | None
    bars: int
    range: float | None
    history_len: int
    median: float | None
    atr: float | None
    equity: float
    trade: bool
    reason: str | None            # why there is no trade
    d: float | None               # stop distance actually used
    shortened: bool | None
    orders: tuple[OrderPlan, ...]
    flatten_at: datetime


def _at(day: date, t: time) -> datetime:
    return datetime.combine(day, t, tzinfo=UTC)


def _tokyo(bars_5m: list[Bar]) -> dict[date, list[Bar]]:
    by_day: dict[date, list[Bar]] = {}
    for b in bars_5m:
        if b.ts.weekday() < 5 and b.ts.time() < TOKYO_END:
            by_day.setdefault(b.ts.date(), []).append(b)
    return by_day


def _floor_lots(lots: float) -> float:
    steps = math.floor(round(lots / LOT_STEP, 9))
    return round(steps * LOT_STEP, 9)


def _tick(price: float) -> float:
    return round(round(price / TICK) * TICK, 2)


def flatten_at(day: date, early_closes: Mapping[date, time] | None = None) -> datetime:
    """When the day's position is market-closed: 20:50, or the open of an early-close session's
    last 5m bar if that is earlier."""
    last_bar = (early_closes or {}).get(day)
    return _at(day, FLATTEN if last_bar is None else min(FLATTEN, last_bar))


def plan_day(
    day: date,
    bars_5m: list[Bar],
    bars_1h: list[Bar],
    equity: float,
    *,
    early_closes: Mapping[date, time] | None = None,
    risk: float = 0.01,
    width_ratio: float = 1.0,
    stop_atr: float = 1.0,
) -> DayPlan:
    """Decide ``day`` once, after its 08:55 bar.

    ``bars_5m`` must cover today's Tokyo window and enough previous weekdays for the yardstick
    (only Tokyo-window bars are used); ``bars_1h`` enough history to seed ATR(4h, 20).
    ``early_closes`` maps a date to the open time (UTC) of that session's last 5m bar."""
    tokyo = _tokyo(bars_5m)
    today = tokyo.get(day, [])
    history = [max(b.high for b in bs) - min(b.low for b in bs)
               for d, bs in sorted(tokyo.items()) if d < day and len(bs) >= MIN_RANGE_BARS]
    history = history[-LOOKBACK:]
    hi = max((b.high for b in today), default=None)
    lo = min((b.low for b in today), default=None)
    rng = hi - lo if hi is not None and lo is not None else None
    med = median(history) if history else None
    atr = wilder_atr(sealed_4h(bars_1h, _at(day, TOKYO_END)))

    flatten = flatten_at(day, early_closes)
    expiry = min(_at(day, EXPIRY), flatten - timedelta(minutes=5))

    reason = None
    if day.weekday() >= 5:
        reason = "weekend"
    elif not today or today[-1].ts.time() != time(8, 55):
        reason = "no_plan_bar"
    elif atr is None:
        reason = "no_atr"
    elif len(today) < MIN_RANGE_BARS:
        reason = "too_few_bars"
    elif len(history) < MIN_LOOKBACK:
        reason = "short_history"
    elif rng is None or med is None or rng < width_ratio * med:
        reason = "narrow_range"

    plan = DayPlan(day=day, tokyo_high=hi, tokyo_low=lo, bars=len(today), range=rng,
                   history_len=len(history), median=med, atr=atr, equity=equity, trade=False,
                   reason=reason, d=None, shortened=None, orders=(), flatten_at=flatten)
    if reason is not None:
        return plan
    assert atr is not None and hi is not None and lo is not None and rng is not None

    off = TRIG_ATR * atr
    risk_distance = stop_atr * atr
    d = min(risk_distance, rng + 2 * off - SL_BUFFER_ATR * atr)
    lots = _floor_lots(risk * equity / (risk_distance * OZ_PER_LOT))
    plan = replace(plan, d=d, shortened=d < risk_distance)
    if lots <= 0:
        return replace(plan, reason="skipped_min_size")
    long_entry, short_entry = hi + off, lo - off
    orders = (
        OrderPlan("buy", _tick(long_entry), _tick(long_entry - d), lots, expiry),
        OrderPlan("sell", _tick(short_entry), _tick(short_entry + d), lots, expiry),
    )
    return replace(plan, trade=True, orders=orders)
