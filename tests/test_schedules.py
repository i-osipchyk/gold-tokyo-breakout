"""One-off EventBridge schedules for early-close days (ARCHITECTURE §4.3): eod at the flatten
time F, watchdogs at F+1 and F+2 min, from data/early_close.yaml."""

import json
from datetime import UTC, date, datetime, time
from pathlib import Path

from gtb.schedules import early_close_schedules, tfvars

NOW = datetime(2026, 10, 11, 17, 0, tzinfo=UTC)


def test_an_early_close_day_gets_eod_at_the_last_bar_and_both_watchdogs_after_it():
    schedules = early_close_schedules({date(2026, 11, 27): time(18, 40)}, now=NOW)

    assert schedules == {
        "eod-20261127": {"run": "eod", "at": "2026-11-27T18:40:00"},
        "watchdog1-20261127": {"run": "watchdog", "at": "2026-11-27T18:41:00"},
        "watchdog2-20261127": {"run": "watchdog", "at": "2026-11-27T18:42:00"},
    }


def test_past_times_and_closes_at_or_after_the_normal_flatten_get_no_schedule():
    schedules = early_close_schedules({
        date(2026, 7, 3): time(16, 55),          # past
        date(2026, 10, 11): time(16, 55),        # today, already gone
        date(2026, 12, 31): time(20, 50),        # the regular 20:50 schedules cover it
    }, now=NOW)

    assert schedules == {}


def test_the_committed_tfvars_match_the_calendar():
    # CI deploys infra/early_close.auto.tfvars.json as committed; regenerate it after editing
    # data/early_close.yaml: uv run python -m gtb.schedules > infra/early_close.auto.tfvars.json
    committed = Path(__file__).parents[1] / "infra" / "early_close.auto.tfvars.json"

    assert json.loads(committed.read_text()) == tfvars()
