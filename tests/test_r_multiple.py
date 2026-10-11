"""r_multiple: a trade's result in R, R = risk x equity at entry (ARCHITECTURE §9.3)."""

from gtb.core import r_multiple


def test_r_is_net_pnl_over_the_risk_budget_at_entry():
    # Spec §7.2, 2026-08-25: -1,030.6 on ~$103,060 equity at 1% is -1.0 R.
    assert r_multiple(-1030.6, equity_at_entry=103_060, risk=0.01) == -1.0
    # Live at 0.25%: +$150 on $30,000 is +2 R.
    assert r_multiple(150.0, equity_at_entry=30_000, risk=0.0025) == 2.0
