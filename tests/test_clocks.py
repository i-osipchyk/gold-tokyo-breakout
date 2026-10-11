"""The two kill-rule clocks, in R (ARCHITECTURE §9.3, docs/REFERENCE.md)."""

from reconcile.clocks import edge_clock, execution_clock


def test_edge_clock_totals_drawdown_and_mean_in_r():
    clock = edge_clock([1.0, -2.0, -1.0, 3.0])

    assert (clock.trades, clock.total_r, clock.drawdown_r, clock.mean_r) == (4, 1.0, -3.0, 0.25)
    assert (clock.drawdown_breached, clock.expectancy_breached) == (False, False)


def test_a_drawdown_worse_than_the_monte_carlo_worst_stops_the_test():
    assert edge_clock([-47.2]).drawdown_breached is False
    assert edge_clock([-47.3]).drawdown_breached is True


def test_expectancy_is_only_judged_after_100_trades():
    assert edge_clock([-0.2] * 99).expectancy_breached is False
    assert edge_clock([-0.2] * 100).expectancy_breached is True
    assert edge_clock([-0.05] * 100).expectancy_breached is False


def test_execution_clock_flags_a_rolling_20_trade_mean_below_minus_005_r():
    assert execution_clock([-0.1] * 19).breached is False          # not 20 trades yet
    assert execution_clock([-0.1] * 19).rolling_mean is None
    assert execution_clock([-0.1] * 20).breached is True
    recovered = execution_clock([-0.1] * 20 + [0.0] * 20)
    assert (recovered.rolling_mean, recovered.breached) == (0.0, False)
