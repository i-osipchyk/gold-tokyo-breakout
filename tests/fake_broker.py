"""In-memory stand-ins for the run handlers' boundaries: the broker (CTraderClient's interface),
the clock and the notifier. Time only moves when a handler sleeps."""

from __future__ import annotations

import itertools
from datetime import timedelta

from gtb.broker import CTraderError, Deal, PendingOrder, Position, SymbolSpec
from gtb.records import DirRecordStore
from gtb.runs import Deps

GOLD = SymbolSpec(symbol_id=41, digits=2, lot_size=10_000, step_volume=100, min_volume=100)
PERIOD = {"M5": timedelta(minutes=5), "H1": timedelta(hours=1)}


class FakeClock:
    def __init__(self, now):
        self._now = now

    def now(self):
        return self._now

    def set(self, t):
        self._now = t

    async def sleep_until(self, t):
        self._now = max(self._now, t)


class FakeNotifier:
    def __init__(self):
        self.sent: list[str] = []

    async def send(self, text):
        self.sent.append(text)


class FakeBroker:
    """A bar is served once it has closed and ``publish_lag`` has passed."""

    def __init__(self, clock, *, m5=(), h1=(), balance=100_000.0,
                 publish_lag=timedelta(seconds=1), latency=timedelta(0)):
        self.clock = clock
        self.bars = {"M5": list(m5), "H1": list(h1)}
        self.publish_lag = publish_lag
        self.latency = latency                        # how long a reconcile takes
        self._balance = balance
        self.positions: dict[int, Position] = {}
        self.orders: dict[int, PendingOrder] = {}
        self.placed: list[PendingOrder] = []         # every accepted placement, in order
        self.reject: dict[str, str] = {}              # side -> error code
        self.deal_list: list[Deal] = []
        self.quotes: list[tuple] = []                 # (time, bid, ask), any order
        self.market: float | None = None              # price a market close fills at
        self.close_failures = 0                       # the next N closes fail
        self.cancelled: list[int] = []
        self.closed: list[int] = []
        self._ids = itertools.count(1000)

    async def balance(self):
        return self._balance

    async def symbol(self, name):
        assert name == "XAUUSD"
        return GOLD

    async def trendbars(self, symbol_id, period, start, end):
        published = self.clock.now() - self.publish_lag
        return [b for b in self.bars[period]
                if start <= b.ts <= end and b.ts + PERIOD[period] <= published]

    async def reconcile(self):
        self.clock.set(self.clock.now() + self.latency)
        return list(self.positions.values()), list(self.orders.values())

    async def place_stop_order(self, symbol, side, *, lots, stop_price, stop_loss, expiry, label,
                               client_order_id):
        if side in self.reject:
            raise CTraderError(self.reject[side], "rejected by the fake")
        order = PendingOrder(
            order_id=next(self._ids), symbol_id=symbol.symbol_id, side=side,
            volume=symbol.volume(lots), stop_price=stop_price, stop_loss=stop_loss, label=label,
            client_order_id=client_order_id, expiry=expiry)
        self.orders[order.order_id] = order
        self.placed.append(order)
        return order.order_id

    async def cancel_order(self, order_id):
        del self.orders[order_id]
        self.cancelled.append(order_id)

    async def close_position(self, position_id, *, volume):
        if self.close_failures:
            self.close_failures -= 1
            raise CTraderError("MARKET_CLOSED", "close failed in the fake")
        p = self.positions[position_id]
        assert volume == p.volume
        self._close(p, at=self.clock.now(), price=self.market, order_id=next(self._ids))
        self.closed.append(position_id)

    async def deals(self, start, end):
        return [d for d in self.deal_list if start <= d.time <= end]

    async def ticks(self, symbol_id, quote, start, end):
        col = 1 if quote == "bid" else 2
        return sorted((q[0], q[col]) for q in self.quotes if start <= q[0] <= end)

    # --- scenario helpers: what the market does between runs ---------------------------

    def fill(self, order_id, *, at, price, commission=-3.5):
        """A resting stop triggers: the order becomes a position."""
        o = self.orders.pop(order_id)
        position_id = next(self._ids)
        self.positions[position_id] = Position(
            position_id=position_id, symbol_id=o.symbol_id, side=o.side, volume=o.volume,
            entry_price=price, stop_loss=o.stop_loss, label=o.label, opened_at=at)
        self.deal_list.append(Deal(
            deal_id=next(self._ids), order_id=order_id, position_id=position_id, side=o.side,
            volume=o.volume, price=price, time=at, commission=commission, closing=False,
            entry_price=None, gross_profit=None))
        return position_id

    def stop_out(self, position_id, *, at, price):
        """The position's stop-loss triggers."""
        self._close(self.positions[position_id], at=at, price=price, order_id=next(self._ids))

    def _close(self, p, *, at, price, order_id, commission=-3.5):
        del self.positions[p.position_id]
        sign = 1 if p.side == "buy" else -1
        self.deal_list.append(Deal(
            deal_id=next(self._ids), order_id=order_id, position_id=p.position_id,
            side="sell" if p.side == "buy" else "buy", volume=p.volume, price=price, time=at,
            commission=commission, closing=True, entry_price=p.entry_price,
            gross_profit=round(sign * (price - p.entry_price) * p.volume / 100, 2)))


def make_deps(tmp_path, now, *, m5=(), h1=(), early_closes=None, **broker):
    """Demo deps at 1% risk around a FakeBroker that shares the run's clock."""
    clock = FakeClock(now)
    return Deps(env="demo", risk=0.01, broker=FakeBroker(clock, m5=m5, h1=h1, **broker),
                store=DirRecordStore(tmp_path), notifier=FakeNotifier(), clock=clock,
                early_closes=early_closes or {}, code_version="abc123")
