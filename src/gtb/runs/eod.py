"""eod: flatten at F + 5 s, then complete the day's record and send the summary
(ARCHITECTURE §4.3). F is 20:50, or earlier on an early-close day."""

from __future__ import annotations

from datetime import timedelta

from ..alerts.messages import alert, eod_summary
from ..core import flatten_at
from ..records import DayRecord
from .deps import SYMBOL, Deps
from .flatten import cancel_orders, close_positions, complete_record, load_record

DELAY = timedelta(seconds=5)
LAST_CHECK = timedelta(minutes=4)          # a minute before the earliest market close


async def eod(deps: Deps) -> DayRecord:
    day = deps.clock.now().date()
    flatten = flatten_at(day, deps.early_closes)
    await deps.clock.sleep_until(flatten + DELAY)
    gold = await deps.broker.symbol(SYMBOL)
    cancelled = await cancel_orders(deps)
    closed, sent = await close_positions(deps, day, until=flatten + LAST_CHECK)

    record, unsent = load_record(deps, day)
    record.alerts += sent
    await complete_record(deps, record, gold, cancelled=cancelled,
                          closed_by=dict.fromkeys(closed, "eod"))
    deps.store.put(record)
    for text in unsent:
        await deps.notifier.send(alert(record, text))
    await deps.notifier.send(eod_summary(record))
    return record
