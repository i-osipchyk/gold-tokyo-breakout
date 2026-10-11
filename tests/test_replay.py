"""plan_day reproduces the reference run's decision on every weekday of 2018-01 -> 2026-08.

Fixtures exported from Hermes (docs/REFERENCE.md): ``static_day_plans.csv`` is what
gold_tokyo_breakout_v2_static decided after each 08:55 bar; ``bars.csv.gz`` the bars it saw.
Each day gets the same history the live bot fetches: 45 days of 5m bars, 90 days of 1h bars.
"""

import csv
import gzip
from bisect import bisect_left
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from gtb.core import Bar, plan_day

FIXTURES = Path(__file__).parent / "fixtures"
BACKTEST_START = datetime(2018, 1, 1, tzinfo=UTC)   # Hermes collected Tokyo ranges from here


def _load_bars():
    tokyo, hourly = [], []
    with gzip.open(FIXTURES / "bars.csv.gz", "rt") as f:
        for row in csv.DictReader(f):
            bar = Bar(datetime.fromtimestamp(int(row["minute"]) * 60, tz=UTC),
                      *(int(row[k]) / 100 for k in ("open", "high", "low", "close")))
            (tokyo if row["kind"] == "t" else hourly).append(bar)
    return [b for b in tokyo if b.ts >= BACKTEST_START], hourly


def _window(bars, times, start, end):
    return bars[bisect_left(times, start):bisect_left(times, end)]


def _opt(value):
    return None if value == "" else float(value)


def test_every_day_plan_matches_the_reference_run():
    tokyo, hourly = _load_bars()
    t_times, h_times = [b.ts for b in tokyo], [b.ts for b in hourly]
    with open(FIXTURES / "static_day_plans.csv") as f:
        rows = list(csv.DictReader(f))

    mismatches = []
    for row in rows:
        day = date.fromisoformat(row["date"])
        nine = datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=9)
        plan = plan_day(
            day,
            _window(tokyo, t_times, nine - timedelta(days=45), nine),
            _window(hourly, h_times, nine - timedelta(days=90), nine),
            equity=float(row["equity"]),
        )
        expected = {
            "trade": row["trade"] == "True",
            "reason": row["reason"] or None,
            "bars": int(row["bars"]),
            "history_len": int(row["history_len"]),
        }
        got = {k: getattr(plan, k) for k in expected}
        ok = got == expected
        ok &= plan.range == pytest.approx(float(row["range"]), abs=1e-9)
        ok &= plan.median == pytest.approx(_opt(row["median"]), abs=1e-9)
        ok &= plan.atr == pytest.approx(float(row["atr"]), rel=1e-3)
        if expected["trade"]:
            buy, sell = plan.orders
            ok &= buy.entry == pytest.approx(float(row["long_entry"]), abs=0.005 + 1e-9)
            ok &= buy.stop_loss == pytest.approx(float(row["long_sl"]), abs=0.005 + 1e-9)
            ok &= sell.entry == pytest.approx(float(row["short_entry"]), abs=0.005 + 1e-9)
            ok &= sell.stop_loss == pytest.approx(float(row["short_sl"]), abs=0.005 + 1e-9)
            ok &= buy.lots == sell.lots == float(row["lots"])
            ok &= plan.shortened == (row["shortened"] == "True")
        if not ok:
            mismatches.append((row["date"], expected, got, plan.atr, row["atr"]))

    assert mismatches == [], f"{len(mismatches)} of {len(rows)} differ, first: {mismatches[:3]}"
