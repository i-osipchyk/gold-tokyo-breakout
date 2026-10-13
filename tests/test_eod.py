"""The eod run (ARCHITECTURE §4.3): flatten at 20:50:05, then complete the day's record from the
broker's deals and ticks."""

import asyncio
from datetime import time, timedelta

from gtb.broker import PendingOrder, Position
from gtb.runs import eod, morning

from fake_broker import make_deps
from synthetic import DAY, history, wide_tokyo

FLATTEN = DAY + timedelta(hours=20, minutes=50)


def _after_morning(tmp_path, **kw):
    """The morning run has placed both stops for DAY."""
    m5, h1 = history()
    tm5, th1 = wide_tokyo()
    deps = make_deps(tmp_path, DAY + timedelta(hours=9), m5=m5 + tm5, h1=h1 + th1, **kw)
    asyncio.run(morning(deps))
    buy, sell = deps.broker.placed
    return deps, buy.order_id, sell.order_id


def _quote(broker, at, bid, ask):
    broker.quotes.append((at, bid, ask))


def test_an_open_position_is_closed_the_rest_cancelled_and_the_trade_recorded(tmp_path):
    deps, buy, sell = _after_morning(tmp_path)
    broker = deps.broker
    entry_at = DAY + timedelta(hours=10, seconds=3)
    _quote(broker, entry_at - timedelta(seconds=1), 2011.10, 2011.25)
    broker.fill(buy, at=entry_at, price=2011.25)
    _quote(broker, FLATTEN + timedelta(seconds=4), 2030.00, 2030.20)
    broker.market = 2030.00
    deps.clock.set(FLATTEN)                               # EventBridge: 20:50:00

    asyncio.run(eod(deps))

    assert broker.cancelled == [sell] and broker.positions == {} and broker.orders == {}
    record = deps.store.get(DAY.date())
    assert [(o.side, o.status) for o in record.orders] == [
        ("buy", "filled"), ("sell", "cancelled")]
    (t,) = record.trades
    assert (t.side, t.entry_time, t.entry_price, t.lots) == ("buy", entry_at, 2011.25, 1.0)
    assert (t.exit_time, t.exit_price, t.exit_reason) == (
        FLATTEN + timedelta(seconds=5), 2030.00, "eod")
    assert (round(t.spread_at_entry, 2), round(t.spread_at_exit, 2)) == (0.15, 0.20)
    # 100 oz x +18.75 = +1,875 gross, -7 commission; R = 1,868 / (1% of 100,000)
    assert (t.commission, t.pnl, t.risk_usd, t.r) == (-7.0, 1868.0, 1000.0, 1.868)
    assert record.alerts == []

    summary = deps.notifier.sent[-1]
    for fact in ("dev", "2025-02-10", "eod", "1,868.00", "1.87"):
        assert fact in summary


def test_a_stop_loss_then_the_opposite_fill_records_two_trades(tmp_path):
    deps, buy, sell = _after_morning(tmp_path)
    broker = deps.broker
    long_pos = broker.fill(buy, at=DAY + timedelta(hours=10), price=2011.2)
    broker.stop_out(long_pos, at=DAY + timedelta(hours=11), price=2001.2)
    broker.fill(sell, at=DAY + timedelta(hours=12), price=1994.8)
    broker.market = 1990.0
    deps.clock.set(FLATTEN)

    asyncio.run(eod(deps))

    record = deps.store.get(DAY.date())
    assert [o.status for o in record.orders] == ["filled", "filled"]
    first, second = record.trades
    assert (first.side, first.exit_reason, first.pnl, first.risk_usd, first.r) == (
        "buy", "sl", -1007.0, 1000.0, -1.007)
    # The sell entered with 100,000 - 1,007 in the account: risk 1% of 98,993.
    assert (second.side, second.exit_reason, second.pnl) == ("sell", "eod", 473.0)
    assert (round(second.risk_usd, 2), round(second.r, 4)) == (989.93, 0.4778)
    assert broker.cancelled == []


def test_an_order_that_never_triggered_has_expired_by_eod(tmp_path):
    deps, buy, sell = _after_morning(tmp_path)
    deps.broker.orders.clear()                            # both expired at 20:05
    deps.clock.set(FLATTEN)

    asyncio.run(eod(deps))

    record = deps.store.get(DAY.date())
    assert [o.status for o in record.orders] == ["expired", "expired"]
    assert record.trades == []
    assert "no trades" in deps.notifier.sent[-1]


def test_a_failed_close_alerts_at_once_and_is_retried_every_5_seconds(tmp_path):
    deps, buy, sell = _after_morning(tmp_path)
    broker = deps.broker
    broker.fill(buy, at=DAY + timedelta(hours=10), price=2011.2)
    broker.market = 2020.0
    broker.close_failures = 2
    alerts_seen_at_close = []
    real_close = broker.close_position

    async def close_watching_alerts(position_id, *, volume):
        alerts_seen_at_close.append(sum(m.startswith("ALERT") for m in deps.notifier.sent))
        await real_close(position_id, volume=volume)

    broker.close_position = close_watching_alerts
    deps.clock.set(FLATTEN)

    asyncio.run(eod(deps))

    assert broker.positions == {}
    assert alerts_seen_at_close == [0, 1, 1]           # the first failure alerted at once
    (t,) = deps.store.get(DAY.date()).trades
    assert (t.exit_reason, t.exit_time) == ("eod", FLATTEN + timedelta(seconds=15))


def test_a_close_that_keeps_failing_alerts_to_close_by_hand_before_the_market_closes(tmp_path):
    deps, buy, sell = _after_morning(tmp_path)
    deps.broker.fill(buy, at=DAY + timedelta(hours=10), price=2011.2)
    deps.broker.close_failures = 10_000
    deps.clock.set(FLATTEN)

    asyncio.run(eod(deps))

    assert len(deps.broker.positions) == 1
    assert deps.clock.now() <= FLATTEN + timedelta(minutes=4)
    assert any(m.startswith("ALERT") and "by hand" in m for m in deps.notifier.sent)
    (t,) = deps.store.get(DAY.date()).trades
    assert (t.exit_time, t.pnl) == (None, None)
    assert "STILL OPEN" in deps.notifier.sent[-1]


def test_orders_and_positions_without_our_label_are_left_alone(tmp_path):
    deps, buy, sell = _after_morning(tmp_path)
    broker = deps.broker
    broker.orders[1] = PendingOrder(
        order_id=1, symbol_id=41, side="buy", volume=100, stop_price=3000.0, stop_loss=None,
        label="manual", client_order_id="", expiry=None)
    broker.positions[2] = Position(
        position_id=2, symbol_id=41, side="sell", volume=100, entry_price=2005.0,
        stop_loss=None, label="", opened_at=DAY)
    deps.clock.set(FLATTEN)

    asyncio.run(eod(deps))

    assert set(broker.orders) == {1} and set(broker.positions) == {2}
    assert sorted(broker.cancelled) == sorted([buy, sell])


def test_without_a_morning_record_eod_still_flattens_and_alerts(tmp_path):
    deps = make_deps(tmp_path, FLATTEN)
    deps.broker.positions[5] = Position(
        position_id=5, symbol_id=41, side="buy", volume=10_000, entry_price=2011.2,
        stop_loss=2001.2, label="gtb", opened_at=DAY + timedelta(hours=10))
    deps.broker.market = 2020.0

    asyncio.run(eod(deps))

    assert deps.broker.positions == {}
    assert any(m.startswith("ALERT") and "no morning record" in m for m in deps.notifier.sent)
    assert "no morning record" in deps.store.get(DAY.date()).alerts[0]


def test_on_an_early_close_day_eod_flattens_5_seconds_after_the_last_bar_opens(tmp_path):
    deps, buy, sell = _after_morning(tmp_path, early_closes={DAY.date(): time(17, 0)})
    deps.broker.fill(buy, at=DAY + timedelta(hours=10), price=2011.2)
    deps.broker.market = 2020.0
    deps.clock.set(DAY + timedelta(hours=17))            # the one-off schedule fires at L

    asyncio.run(eod(deps))

    (t,) = deps.store.get(DAY.date()).trades
    assert (t.exit_reason, t.exit_time) == ("eod", DAY + timedelta(hours=17, seconds=5))
