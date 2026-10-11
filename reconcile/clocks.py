"""The kill-rule clocks (ARCHITECTURE §9.3); thresholds from docs/REFERENCE.md.

Edge clock: every Hermes-variant replay trade since the rules were frozen (2026-10-11).
Execution clock: live R - replay R per trade, from the first live trade."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from statistics import fmean

DRAWDOWN_LIMIT_R = -47.2          # the variant's Monte Carlo worst drawdown in R
EXPECTANCY_MIN_TRADES = 100
EXPECTANCY_LIMIT_R = -0.10
EXECUTION_WINDOW = 20
EXECUTION_LIMIT_R = -0.05


@dataclass(frozen=True)
class EdgeClock:
    trades: int
    total_r: float
    drawdown_r: float             # worst peak-to-trough of cumulative R (<= 0)
    mean_r: float | None
    drawdown_breached: bool
    expectancy_breached: bool


@dataclass(frozen=True)
class ExecutionClock:
    trades: int
    rolling_mean: float | None    # mean of the last 20 (live - replay) R, once there are 20
    breached: bool


def edge_clock(rs: Sequence[float]) -> EdgeClock:
    total = peak = drawdown = 0.0
    for r in rs:
        total += r
        peak = max(peak, total)
        drawdown = min(drawdown, total - peak)
    mean = fmean(rs) if rs else None
    return EdgeClock(
        trades=len(rs), total_r=total, drawdown_r=drawdown, mean_r=mean,
        drawdown_breached=drawdown < DRAWDOWN_LIMIT_R,
        expectancy_breached=len(rs) >= EXPECTANCY_MIN_TRADES and mean is not None
        and mean < EXPECTANCY_LIMIT_R)


def execution_clock(diffs: Sequence[float]) -> ExecutionClock:
    if len(diffs) < EXECUTION_WINDOW:
        return ExecutionClock(trades=len(diffs), rolling_mean=None, breached=False)
    mean = fmean(diffs[-EXECUTION_WINDOW:])
    return ExecutionClock(trades=len(diffs), rolling_mean=mean, breached=mean < EXECUTION_LIMIT_R)
