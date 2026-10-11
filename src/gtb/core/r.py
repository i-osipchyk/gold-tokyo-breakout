from __future__ import annotations


def r_multiple(net_pnl: float, *, equity_at_entry: float, risk: float) -> float:
    """A trade's net result in R; one R is the risk budget, ``risk`` x equity at entry."""
    return net_pnl / (risk * equity_at_entry)
