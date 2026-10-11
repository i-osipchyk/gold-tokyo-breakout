"""The watchdogs (ARCHITECTURE §4.3): from F+1 / F+2 min, every 30 s until F+4 min, close any
open gtb position and alert."""

import asyncio
from datetime import timedelta

from gtb.runs import eod, morning, watchdog

from fake_broker import make_deps
from synthetic import DAY, history, wide_tokyo

FLATTEN = DAY + timedelta(hours=20, minutes=50)


def _filled_buy(tmp_path):
    m5, h1 = history()
    tm5, th1 = wide_tokyo()
    deps = make_deps(tmp_path, DAY + timedelta(hours=9), m5=m5 + tm5, h1=h1 + th1)
    asyncio.run(morning(deps))
    buy, _ = deps.broker.placed
    deps.broker.fill(buy.order_id, at=DAY + timedelta(hours=10), price=2011.2)
    deps.broker.market = 2020.0
    return deps


def test_an_open_position_is_closed_alerted_and_recorded(tmp_path):
    deps = _filled_buy(tmp_path)                          # and eod never ran
    deps.clock.set(FLATTEN + timedelta(minutes=1))
    sent_before = len(deps.notifier.sent)

    asyncio.run(watchdog(deps))

    assert deps.broker.positions == {}
    alerts = [m for m in deps.notifier.sent[sent_before:] if m.startswith("ALERT")]
    assert len(alerts) == 1 and "watchdog" in alerts[0]
    record = deps.store.get(DAY.date())
    (t,) = record.trades
    assert (t.exit_reason, t.exit_time, t.pnl) == (
        "watchdog", FLATTEN + timedelta(minutes=1), 873.0)
    assert any("watchdog" in a for a in record.alerts)


def test_after_a_clean_eod_the_watchdog_is_quiet_and_watches_until_f_plus_4(tmp_path):
    deps = _filled_buy(tmp_path)
    deps.clock.set(FLATTEN)
    asyncio.run(eod(deps))
    after_eod = deps.store.get(DAY.date())
    sent_before = len(deps.notifier.sent)
    deps.clock.set(FLATTEN + timedelta(minutes=2))

    asyncio.run(watchdog(deps))

    assert deps.notifier.sent[sent_before:] == []
    assert deps.store.get(DAY.date()) == after_eod
    assert deps.clock.now() == FLATTEN + timedelta(minutes=4)


def test_the_last_check_is_at_f_plus_4_even_when_broker_calls_take_time(tmp_path):
    # Seen locally: with ~0.3 s per check, 20:53:30.3 + 30 s overshot 20:54:00 and the run
    # stopped a check early.
    deps = make_deps(tmp_path, FLATTEN + timedelta(minutes=1),
                     latency=timedelta(milliseconds=300))

    asyncio.run(watchdog(deps))

    assert deps.clock.now() >= FLATTEN + timedelta(minutes=4)
