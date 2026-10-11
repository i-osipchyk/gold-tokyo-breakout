"""plan_day: the whole trading decision for one day (ARCHITECTURE §2, §4.1), on the synthetic
bars of tests/synthetic.py (ATR exactly 10, median range 10)."""

from datetime import time, timedelta

from gtb.core import plan_day

from synthetic import DAY
from synthetic import history as _history
from synthetic import wide_tokyo as _wide_tokyo


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
