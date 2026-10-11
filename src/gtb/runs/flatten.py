"""Flattening and completing the day's record, shared by eod and the watchdogs.

Only orders and positions labelled ``gtb`` are touched. Trades are rebuilt from the broker's deals
every time, so eod and the watchdogs can each write the record without coordinating."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from ..alerts.messages import alert_text
from ..broker import CTraderError, Deal, SymbolSpec
from ..core import r_multiple
from ..records import DayRecord, TradeRecord
from .deps import LABEL, Deps

SPREAD_LOOKBACK = timedelta(seconds=60)
RETRY_EVERY = timedelta(seconds=5)


async def cancel_orders(deps: Deps) -> list[int]:
    """Cancel every pending ``gtb`` order; returns their ids."""
    _, orders = await deps.broker.reconcile()
    mine = [o.order_id for o in orders if o.label == LABEL]
    for order_id in mine:
        await deps.broker.cancel_order(order_id)
    return mine


async def close_positions(deps: Deps, day: date, *,
                          until: datetime) -> tuple[list[int], list[str]]:
    """Market-close every open ``gtb`` position, retrying every 5 s until ``until``. The first
    failure is alerted at once, and again if a position is still open at the end. Returns the
    closed position ids and the alerts sent."""
    closed: list[int] = []
    sent: list[str] = []

    async def send(text: str) -> None:
        sent.append(text)
        await deps.notifier.send(alert_text(deps.env, day, text))

    attempts = 0
    while True:
        attempts += 1
        positions, _ = await deps.broker.reconcile()
        error = None
        for p in positions:
            if p.label != LABEL:
                continue
            try:
                await deps.broker.close_position(p.position_id, volume=p.volume)
                closed.append(p.position_id)
            except (CTraderError, ConnectionError, TimeoutError) as e:
                error = f"close of {p.side} position {p.position_id} failed: {e}"
        if error is None:
            return closed, sent
        if not sent:
            await send(f"{error}; retrying every {RETRY_EVERY.seconds} s")
        now = deps.clock.now()
        if now >= until:
            await send(f"{error} after {attempts} attempts; position still open — "
                       "close it by hand before the market closes")
            return closed, sent
        await deps.clock.sleep_until(min(now + RETRY_EVERY, until))


def load_record(deps: Deps, day: date) -> tuple[DayRecord, list[str]]:
    """Today's record, and any alerts added to it here (not yet sent)."""
    record = deps.store.get(day)
    if record is not None:
        return record, []
    record = DayRecord(date=day, env=deps.env, code_version=deps.code_version)
    record.alerts.append("no morning record for today")
    return record, list(record.alerts)


async def _spread(deps: Deps, gold: SymbolSpec, at: datetime) -> float | None:
    """Ask - bid as quoted at ``at``: the last tick of each at or before it."""
    start = at - SPREAD_LOOKBACK
    bid = await deps.broker.ticks(gold.symbol_id, "bid", start, at)
    ask = await deps.broker.ticks(gold.symbol_id, "ask", start, at)
    if not bid or not ask:
        return None
    return ask[-1][1] - bid[-1][1]


async def complete_record(deps: Deps, record: DayRecord, gold: SymbolSpec, *,
                          cancelled: list[int], closed_by: dict[int, str]) -> None:
    """Order statuses and trades from the day's deals. ``closed_by`` maps the positions this run
    closed to the exit reason ("eod" | "watchdog"); other closes keep their recorded reason, or
    are stop-losses."""
    day_start = datetime.combine(record.date, time(0), tzinfo=UTC)
    deals = await deps.broker.deals(day_start, deps.clock.now())
    ours = {o.order_id for o in record.orders if o.order_id is not None}
    opening = [d for d in deals if not d.closing and d.order_id in ours]
    closing: dict[int, Deal] = {d.position_id: d for d in deals if d.closing}
    filled = {d.order_id for d in opening}

    for o in record.orders:
        if o.order_id in filled:
            o.status = "filled"
        elif o.order_id in cancelled:
            o.status = "cancelled"
        elif o.status == "placed":
            o.status = "expired"

    previous = {t.position_id: t.exit_reason for t in record.trades}
    equity = record.inputs.equity if record.inputs is not None else 0.0
    trades: list[TradeRecord] = []
    for d in opening:
        c = closing.get(d.position_id)
        commission = d.commission + (c.commission if c is not None else 0.0)
        pnl = None if c is None or c.gross_profit is None else c.gross_profit + commission
        at_entry = equity + sum(t.pnl for t in trades
                                if t.pnl is not None and t.exit_time is not None
                                and t.exit_time <= d.time)
        trades.append(TradeRecord(
            side=d.side, entry_time=d.time, entry_price=d.price,
            spread_at_entry=await _spread(deps, gold, d.time),
            exit_time=None if c is None else c.time, exit_price=None if c is None else c.price,
            spread_at_exit=None if c is None else await _spread(deps, gold, c.time),
            exit_reason=None if c is None else closed_by.get(
                d.position_id, previous.get(d.position_id) or "sl"),
            lots=d.volume / gold.lot_size, commission=commission, pnl=pnl,
            risk_usd=deps.risk * at_entry,
            r=None if pnl is None else r_multiple(pnl, equity_at_entry=at_entry, risk=deps.risk),
            position_id=d.position_id))
    record.trades = trades
