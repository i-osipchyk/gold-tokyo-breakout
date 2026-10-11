"""One-off schedules for early-close days (ARCHITECTURE §4.3), generated at deploy time:

    uv run python -m gtb.schedules > infra/early_close.auto.tfvars.json

eod fires at the day's flatten time F (it sleeps to F + 5 s itself), the watchdogs at F+1 and F+2
min. Days whose flatten is the normal 20:50 are covered by the regular schedules."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta

from .config import CALENDAR, load_early_closes
from .core import flatten_at

NORMAL_FLATTEN = time(20, 50)
RUNS = (("eod", "eod", 0), ("watchdog1", "watchdog", 1), ("watchdog2", "watchdog", 2))


def early_close_schedules(early_closes: Mapping[date, time], *,
                          now: datetime) -> dict[str, dict[str, str]]:
    """Name -> {"run", "at" (UTC, no zone)} for every one-off run still ahead of ``now``."""
    out = {}
    for day in sorted(early_closes):
        f = flatten_at(day, early_closes)
        if f.time() >= NORMAL_FLATTEN:
            continue
        for name, run, minutes in RUNS:
            at = f + timedelta(minutes=minutes)
            if at > now:
                out[f"{name}-{day:%Y%m%d}"] = {"run": run, "at": at.strftime("%Y-%m-%dT%H:%M:%S")}
    return out


if __name__ == "__main__":
    schedules = early_close_schedules(load_early_closes(CALENDAR), now=datetime.now(UTC))
    json.dump({"early_close_schedules": schedules}, sys.stdout, indent=2)
    print()
