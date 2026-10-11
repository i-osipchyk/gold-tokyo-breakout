"""heartbeat (09:10): alert if today's morning record is missing -- it catches a morning run that
never started -- and refresh the cTrader access token once it is a week old (ARCHITECTURE §4.3,
§5), far from its ~30-day expiry and away from the trading runs."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Protocol

from ..alerts.messages import alert_text
from .deps import Deps

REFRESH_AFTER = timedelta(days=7)


class TokenKeeper(Protocol):
    def refreshed_at(self) -> datetime | None: ...

    async def refresh(self, now: datetime) -> None: ...


async def heartbeat(deps: Deps, tokens: TokenKeeper) -> None:
    now = deps.clock.now()
    day = now.date()
    if deps.store.get(day) is None:
        await deps.notifier.send(alert_text(
            deps.env, day, "no morning record by 09:10: the morning run did not finish"))
    last = tokens.refreshed_at()
    if last is None or now - last >= REFRESH_AFTER:
        try:
            await tokens.refresh(now)
        except Exception as e:                   # any failure: a person has to look at it
            await deps.notifier.send(alert_text(
                deps.env, day, f"cTrader token refresh failed ({type(e).__name__}: {e}); "
                f"last refreshed {last or 'never'}, tokens last ~30 days"))
