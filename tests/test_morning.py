"""The morning run (ARCHITECTURE §4.3): plan after the 08:55 bar, place both stops once, record."""

import asyncio
from datetime import timedelta

from gtb.broker import PendingOrder
from gtb.runs import morning

from fake_broker import make_deps
from synthetic import DAY, history, wide_tokyo

FIRES = DAY + timedelta(hours=9)                 # EventBridge: 09:00:00


def _setup(tmp_path, tokyo=wide_tokyo, **broker):
    m5, h1 = history()
    tm5, th1 = tokyo()
    return make_deps(tmp_path, FIRES, m5=m5 + tm5, h1=h1 + th1, **broker)


def test_trade_day_places_both_stops_and_records_the_plan(tmp_path):
    deps = _setup(tmp_path)

    asyncio.run(morning(deps))

    placed = deps.broker.placed
    assert [(o.side, o.stop_price, o.stop_loss, o.volume, o.label, o.client_order_id)
            for o in placed] == [
        ("buy", 2011.2, 2001.2, 10_000, "gtb", "gtb-20250210-L"),
        ("sell", 1994.8, 2004.8, 10_000, "gtb", "gtb-20250210-S"),
    ]
    assert {o.expiry for o in placed} == {DAY + timedelta(hours=20, minutes=5)}

    record = deps.store.get(DAY.date())
    assert (record.env, record.code_version) == ("demo", "abc123")
    i = record.inputs
    assert (i.tokyo_high, i.tokyo_low, i.bars, i.range, i.history_len, i.median, i.atr,
            i.equity) == (2011, 1995, 108, 16, 20, 10, 10, 100_000)
    assert (i.plan_time, i.attempts) == (FIRES + timedelta(seconds=10), 1)
    assert (record.decision.trade, record.decision.reason) == (True, None)
    assert [(o.side, o.entry, o.sl, o.d, o.shortened, o.lots, o.client_order_id, o.status)
            for o in record.orders] == [
        ("buy", 2011.2, 2001.2, 10, False, 1.0, "gtb-20250210-L", "placed"),
        ("sell", 1994.8, 2004.8, 10, False, 1.0, "gtb-20250210-S", "placed"),
    ]

    (summary,) = deps.notifier.sent
    for fact in ("demo", "2025-02-10", "2011.2", "1994.8", "1.0"):
        assert fact in summary


def test_a_second_morning_run_is_a_no_op(tmp_path):
    deps = _setup(tmp_path)
    asyncio.run(morning(deps))
    first = deps.store.get(DAY.date())

    deps.clock.set(FIRES + timedelta(seconds=30))               # a retried invocation
    asyncio.run(morning(deps))

    assert len(deps.broker.placed) == 2
    assert deps.store.get(DAY.date()) == first
    assert len(deps.notifier.sent) == 1


def test_an_order_already_at_the_broker_is_not_placed_again(tmp_path):
    # A previous invocation placed the buy stop, then died before writing the record.
    deps = _setup(tmp_path)
    deps.broker.orders[77] = PendingOrder(
        order_id=77, symbol_id=41, side="buy", volume=10_000, stop_price=2011.2,
        stop_loss=2001.2, label="gtb", client_order_id="gtb-20250210-L",
        expiry=DAY + timedelta(hours=20, minutes=5))

    asyncio.run(morning(deps))

    assert [o.client_order_id for o in deps.broker.placed] == ["gtb-20250210-S"]
    record = deps.store.get(DAY.date())
    assert [(o.client_order_id, o.status) for o in record.orders] == [
        ("gtb-20250210-L", "placed"), ("gtb-20250210-S", "placed")]
    assert record.orders[0].order_id == 77


def test_a_late_0855_bar_is_retried_every_15_seconds(tmp_path):
    # The bar is served 40 s after it closes: missing at 09:00:10 and :25, there at :40.
    deps = _setup(tmp_path, publish_lag=timedelta(seconds=40))

    asyncio.run(morning(deps))

    record = deps.store.get(DAY.date())
    assert (record.inputs.plan_time, record.inputs.attempts) == (FIRES + timedelta(seconds=40), 3)
    assert (record.decision.trade, len(deps.broker.placed)) == (True, 2)


def test_no_0855_bar_by_0905_misses_the_day_with_an_alert(tmp_path):
    deps = _setup(tmp_path, publish_lag=timedelta(minutes=10))

    asyncio.run(morning(deps))

    assert deps.broker.placed == []
    record = deps.store.get(DAY.date())
    assert (record.decision.trade, record.decision.reason) == (False, "cutoff_missed")
    assert record.inputs.attempts == 20                       # 09:00:10, :25, ... 09:04:55
    assert deps.clock.now() <= FIRES + timedelta(minutes=5)
    assert len(record.alerts) == 1 and "cutoff" in record.alerts[0]
    assert any("cutoff" in m and "ALERT" in m for m in deps.notifier.sent)


def test_size_below_the_minimum_skips_the_day_with_an_alert(tmp_path):
    deps = _setup(tmp_path, balance=500.0)      # 1% of $500 over a 10 x 100 oz stop: 0.005 lot

    asyncio.run(morning(deps))

    assert deps.broker.placed == []
    record = deps.store.get(DAY.date())
    assert (record.decision.trade, record.decision.reason) == (False, "skipped_min_size")
    assert len(record.alerts) == 1 and "skipped_min_size" in record.alerts[0]
    assert any(m.startswith("ALERT") and "skipped_min_size" in m for m in deps.notifier.sent)


def test_a_rejected_order_is_recorded_and_alerted_and_the_other_side_still_placed(tmp_path):
    deps = _setup(tmp_path)
    deps.broker.reject["buy"] = "NOT_ENOUGH_MONEY"

    asyncio.run(morning(deps))

    assert [o.side for o in deps.broker.placed] == ["sell"]
    record = deps.store.get(DAY.date())
    assert [(o.side, o.status, o.order_id) for o in record.orders] == [
        ("buy", "rejected", None), ("sell", "placed", deps.broker.placed[0].order_id)]
    assert len(record.alerts) == 1 and "NOT_ENOUGH_MONEY" in record.alerts[0]
    assert any(m.startswith("ALERT") and "NOT_ENOUGH_MONEY" in m for m in deps.notifier.sent)


def test_a_run_starting_after_the_cutoff_places_nothing(tmp_path):
    # Lambda re-invokes a failed async run ~1 and ~2 minutes later; at 09:07 the day is missed
    # even though the 08:55 bar is there.
    deps = _setup(tmp_path)
    deps.clock.set(FIRES + timedelta(minutes=7))

    asyncio.run(morning(deps))

    assert deps.broker.placed == []
    record = deps.store.get(DAY.date())
    assert (record.decision.trade, record.decision.reason) == (False, "cutoff_missed")
    assert any(m.startswith("ALERT") and "cutoff" in m for m in deps.notifier.sent)
