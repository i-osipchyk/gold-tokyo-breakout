"""watchdog: two independent invocations at F+1 and F+2 min re-check every 30 s until F+4 min,
a minute before the earliest market close, and close any gtb position left open
(ARCHITECTURE §4.3). Quiet when the account is flat."""

from __future__ import annotations

from datetime import timedelta

from ..alerts.messages import alert_text
from ..core import flatten_at
from .deps import LABEL, SYMBOL, Deps
from .flatten import close_positions, complete_record, load_record

CHECK_EVERY = timedelta(seconds=30)
LAST_CHECK = timedelta(minutes=4)


async def watchdog(deps: Deps) -> None:
    day = deps.clock.now().date()
    until = flatten_at(day, deps.early_closes) + LAST_CHECK
    while True:
        positions, _ = await deps.broker.reconcile()
        mine = [p for p in positions if p.label == LABEL]
        if mine:
            text = (f"watchdog: {len(mine)} gtb position(s) open after the flatten "
                    f"({', '.join(f'{p.side} {p.position_id}' for p in mine)}), closing")
            await deps.notifier.send(alert_text(deps.env, day, text))
            closed, sent = await close_positions(deps, day, until=until)
            record, unsent = load_record(deps, day)
            record.alerts += [text, *sent]
            gold = await deps.broker.symbol(SYMBOL)
            await complete_record(deps, record, gold, cancelled=[],
                                  closed_by=dict.fromkeys(closed, "watchdog"))
            deps.store.put(record)
            for t in unsent:
                await deps.notifier.send(alert_text(deps.env, day, t))
        now = deps.clock.now()
        if now >= until:
            return
        await deps.clock.sleep_until(min(now + CHECK_EVERY, until))
