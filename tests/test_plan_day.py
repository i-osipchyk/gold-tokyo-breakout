"""plan_day: the whole trading decision for one day (ARCHITECTURE §2, §4.1).

Synthetic bars in winter (EST): 4h buckets start at 22/02/06/10/14/18 UTC, so at 09:00 the bucket
06:00-10:00 is still unsealed. Quiet bars are level +/- 5, so every sealed 4h true range is 10
and ATR(4h, 20) is exactly 10.
"""

from datetime import UTC, datetime, time, timedelta

from gtb.core import Bar, plan_day

DAY = datetime(2025, 2, 10, tzinfo=UTC)          # Monday
HISTORY_START = DAY - timedelta(days=35)         # 25 weekdays before DAY


def _bars(start, step, n, level=2000.0, half=5.0):
    return [Bar(start + step * i, level, level + half, level - half, level) for i in range(n)]


def _weekdays(start, end):
    day = start
    while day < end:
        if day.weekday() < 5:
            yield day
        day += timedelta(days=1)


def _history():
    """Tokyo 5m bars and 1h bars (all day) at 2000 +/- 5 for the weekdays before DAY."""
    m5, h1 = [], []
    for day in _weekdays(HISTORY_START, DAY):
        m5 += _bars(day, timedelta(minutes=5), 108)
        h1 += _bars(day, timedelta(hours=1), 24)
    return m5, h1


def _wide_tokyo():
    """00:00-06:00 at 2000 +/- 5, 06:00-09:00 at 2006 +/- 5: range 1995-2011 = 16."""
    m5 = _bars(DAY, timedelta(minutes=5), 72) + _bars(
        DAY + timedelta(hours=6), timedelta(minutes=5), 36, level=2006.0)
    h1 = _bars(DAY, timedelta(hours=1), 6) + _bars(
        DAY + timedelta(hours=6), timedelta(hours=1), 3, level=2006.0)
    return m5, h1


def test_wide_tokyo_range_plans_both_stop_orders():
    m5, h1 = _history()
    tm5, th1 = _wide_tokyo()

    plan = plan_day(DAY.date(), m5 + tm5, h1 + th1, equity=100_000)

    assert (plan.trade, plan.reason) == (True, None)
    assert (plan.tokyo_high, plan.tokyo_low, plan.bars) == (2011, 1995, 108)
    assert (plan.range, plan.history_len, plan.median, plan.atr) == (16, 20, 10, 10)
    assert plan.shortened is False
    assert plan.flatten_at == DAY + timedelta(hours=20, minutes=50)
    buy, sell = plan.orders
    assert (buy.side, buy.entry, buy.stop_loss, buy.lots) == ("buy", 2011.2, 2001.2, 1.0)
    assert (sell.side, sell.entry, sell.stop_loss, sell.lots) == ("sell", 1994.8, 2004.8, 1.0)
    assert buy.expiry == sell.expiry == DAY + timedelta(hours=20, minutes=5)


def test_early_close_moves_the_flatten_to_the_last_bar_and_expiry_two_bars_before_it():
    m5, h1 = _history()
    tm5, th1 = _wide_tokyo()

    plan = plan_day(DAY.date(), m5 + tm5, h1 + th1, equity=100_000,
                    early_closes={DAY.date(): time(17, 0)})

    assert plan.flatten_at == DAY + timedelta(hours=17)
    assert {o.expiry for o in plan.orders} == {DAY + timedelta(hours=16, minutes=55)}


def test_size_below_one_hundredth_of_a_lot_skips_the_day_without_orders():
    # $500 at 1% over a 10 x 100 oz stop = 0.005 lot, which floors to 0.
    m5, h1 = _history()
    tm5, th1 = _wide_tokyo()

    plan = plan_day(DAY.date(), m5 + tm5, h1 + th1, equity=500)

    assert (plan.trade, plan.reason, plan.orders) == (False, "skipped_min_size", ())


def test_no_plan_without_the_0855_bar():
    m5, h1 = _history()
    tm5, th1 = _wide_tokyo()

    plan = plan_day(DAY.date(), m5 + tm5[:-1], h1 + th1, equity=100_000)

    assert (plan.trade, plan.reason, plan.orders) == (False, "no_plan_bar", ())
