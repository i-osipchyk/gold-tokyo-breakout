"""heartbeat (09:10): alert if today's morning record is missing -- it catches a morning run that
never started (ARCHITECTURE §4.3). The cTrader token is the platform's to refresh, not ours."""

from __future__ import annotations

from ..alerts.messages import alert_text
from .deps import Deps


async def heartbeat(deps: Deps) -> None:
    day = deps.clock.now().date()
    if deps.store.get(day) is None:
        await deps.notifier.send(alert_text(
            deps.env, day, "no morning record by 09:10: the morning run did not finish"))
