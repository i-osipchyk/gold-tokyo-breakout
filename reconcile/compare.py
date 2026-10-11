"""The three-layer comparison (ARCHITECTURE §4.5). Pure: records and a replay in, a report out."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from gtb.core import Bar
from gtb.records import DayRecord, TradeRecord

from .replay import Replay, ReplayDay, ReplayTrade

ATR_TOLERANCE = 0.001             # relative
EXACT = 1e-6                      # prices come from the same integer-cent bars
OZ_PER_LOT = 100
LOT_STEP = 0.01
HALF_TICK = 0.005 + 1e-9
TYPICAL_SPREAD = 0.12             # Hermes' Razor spread for XAUUSD, when no ticks were recorded
BAR = timedelta(minutes=5)          # live levels are rounded to the tick, the replay's are not


@dataclass(frozen=True)
class Finding:
    day: date
    layer: int
    field: str
    live: Any
    replay: Any
    status: str                   # ok | mismatch | explained | missing
    note: str = ""


@dataclass(frozen=True)
class ExecutionRow:
    """Layer 3: one trade both sides took."""

    day: date
    side: str
    live_entry: float
    replay_entry: float
    live_exit: float | None
    replay_exit: float
    spread_at_entry: float | None
    spread_at_exit: float | None
    commission: float
    live_r: float | None
    replay_r: float

    @property
    def r_diff(self) -> float | None:
        return None if self.live_r is None else self.live_r - self.replay_r


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    execution: list[ExecutionRow] = field(default_factory=list)


def _same(a: Any, b: Any, tol: float = EXACT) -> bool:
    if a is None or b is None:
        return a is b
    if isinstance(a, bool) or isinstance(b, bool) or isinstance(a, str) or isinstance(b, str):
        return bool(a == b)
    return abs(float(a) - float(b)) <= tol


def _budget_share(lots: float, atr: float, equity: float, risk: float) -> float:
    """Risk taken (lots x 1 ATR x 100 oz) as a share of the risk budget (risk x equity)."""
    return lots * atr * OZ_PER_LOT / (risk * equity)


def compare(records: Iterable[DayRecord], replay: Replay, *, risk: float) -> Report:
    report = Report()
    records = sorted(records, key=lambda r: r.date)
    by_day = {r.date: r for r in records}
    # From the first live record to the end of the replay: a run that never happened at the end
    # of the window is a missing day too.
    window = (records[0].date, max([records[-1].date, *replay.days])) if records else None
    days = sorted(set(by_day) | {d for d in replay.days
                                 if window and window[0] <= d <= window[1]})
    for d in days:
        record, day = by_day.get(d), replay.days.get(d)
        if record is None:
            report.findings.append(Finding(d, 1, "record", None, "planned", "missing",
                                           "no live record: the bot did not run"))
        elif day is None:
            if record.decision is None or record.decision.reason != "weekend":
                report.findings.append(Finding(d, 1, "replay", "record", None, "missing",
                                               "no replay plan for this day"))
        else:
            _layer1(report, record, day)
            _layer2_levels(report, record, day, risk, replay.risk)
            _layer2_triggers(report, record, replay)
            _layer3(report, record, day, replay)
    return report


def _layer1(report: Report, record: DayRecord, day: ReplayDay) -> None:
    i, d = record.inputs, record.date
    if i is None or record.decision is None:
        report.findings.append(Finding(d, 1, "inputs", None, "planned", "missing",
                                       "the morning part of the record is missing"))
        return
    for name in ("tokyo_high", "tokyo_low", "bars", "range", "history_len", "median"):
        live, rep = getattr(i, name), getattr(day, name)
        report.findings.append(Finding(d, 1, name, live, rep,
                                       "ok" if _same(live, rep) else "mismatch"))
    ok = i.atr is not None and day.atr is not None and \
        abs(i.atr - day.atr) <= ATR_TOLERANCE * abs(day.atr)
    report.findings.append(Finding(d, 1, "atr", i.atr, day.atr,
                                   "ok" if ok or i.atr is day.atr is None else "mismatch"))
    live = (record.decision.trade, record.decision.reason)
    rep = (day.trade, day.reason)
    if record.decision.reason == "cutoff_missed":
        report.findings.append(Finding(d, 1, "decision", live, rep, "missing",
                                       "cutoff missed: no plan live"))
    else:
        report.findings.append(Finding(d, 1, "decision", live, rep,
                                       "ok" if live == rep else "mismatch"))


def _layer2_levels(report: Report, record: DayRecord, day: ReplayDay, risk: float,
                   replay_risk: float) -> None:
    d = record.date
    if not day.trade or not record.orders:
        return
    replay_levels = {"buy": (day.long_entry, day.long_sl), "sell": (day.short_entry, day.short_sl)}
    for o in record.orders:
        for name, live, rep in zip(("entry", "sl"), (o.entry, o.sl), replay_levels[o.side],
                                   strict=True):
            report.findings.append(Finding(d, 2, f"{o.side}.{name}", live, rep,
                                           "ok" if _same(live, rep, HALF_TICK) else "mismatch"))
    i = record.inputs
    if i is not None and i.atr and day.atr and day.lots is not None:
        live = _budget_share(record.orders[0].lots, i.atr, i.equity, risk)
        rep = _budget_share(day.lots, day.atr, day.equity, replay_risk)
        # Lots floor to 0.01 on each side, at different equities.
        tol = _budget_share(LOT_STEP, i.atr, i.equity, risk) + \
            _budget_share(LOT_STEP, day.atr, day.equity, replay_risk)
        report.findings.append(Finding(d, 2, "risk", round(live, 4), round(rep, 4),
                                       "ok" if abs(live - rep) <= tol else "mismatch",
                                       "share of the risk budget"))
    shortened = record.orders[0].shortened
    report.findings.append(Finding(d, 2, "shortened", shortened, day.shortened,
                                   "ok" if shortened == day.shortened else "mismatch"))


def _bar_of(t: datetime) -> datetime:
    return t - timedelta(minutes=t.minute % 5, seconds=t.second, microseconds=t.microsecond)


def _layer2_triggers(report: Report, record: DayRecord, replay: Replay) -> None:
    """Which side filled in which 5m bar. cTrader triggers a buy stop on the ask, Hermes on the
    bid: a live buy that the replay filled later or never is explained when the bid in the live
    fill's bar came within one spread of the entry."""
    d = record.date
    bars = {b.ts: b for b in replay.bars if b.ts.date() == d}
    levels = {o.side: o.entry for o in record.orders}
    for side in ("buy", "sell"):
        live = next((t for t in record.trades if t.side == side), None)
        rep = next((t for t in replay.trades
                    if t.side == side and t.entry_time.date() == d), None)
        if live is None and rep is None:
            continue
        live_bar = None if live is None else _bar_of(live.entry_time)
        rep_bar = None if rep is None else rep.entry_time
        status, note = "mismatch", ""
        if live_bar == rep_bar:
            status = "ok"
        elif side == "buy" and live is not None and live_bar is not None and \
                (rep_bar is None or live_bar < rep_bar) and live_bar in bars:
            spread = live.spread_at_entry or TYPICAL_SPREAD
            gap = levels.get("buy", rep.entry_price if rep else 0.0) - bars[live_bar].high
            if 0 <= gap <= spread:
                status = "explained"
                note = (f"ask-side marginal touch: bid high {bars[live_bar].high} was {gap:.3f} "
                        f"below the entry, within the {spread:.2f} spread")
        report.findings.append(Finding(d, 2, f"{side}.trigger", live_bar, rep_bar, status, note))
        if live is not None and rep is not None and live.exit_time is not None:
            _layer2_exit(report, record, live, rep, bars)


_REPLAY_EXIT = {"stop_loss": "sl", "signal": "eod"}


def _layer2_exit(report: Report, record: DayRecord, live: TradeRecord, rep: ReplayTrade,
                 bars: dict[datetime, Bar]) -> None:
    """Exit reason and bar. A short's stop-loss is a buy stop, triggered on the ask live: a live
    short stopped earlier than in the replay is explained by a bid within one spread of the SL."""
    assert live.exit_time is not None
    live_exit = (live.exit_reason, _bar_of(live.exit_time))
    rep_exit = (_REPLAY_EXIT.get(rep.exit_reason, rep.exit_reason), rep.exit_time)
    status, note = ("ok" if live_exit == rep_exit else "mismatch"), ""
    sl = next((o.sl for o in record.orders if o.side == live.side), None)
    bar = bars.get(live_exit[1])
    if status == "mismatch" and live.side == "sell" and live.exit_reason == "sl" and \
            live_exit[1] < rep_exit[1] and bar is not None and sl is not None:
        spread = live.spread_at_exit or TYPICAL_SPREAD
        gap = sl - bar.high
        if 0 <= gap <= spread:
            status = "explained"
            note = (f"ask-side marginal touch: bid high {bar.high} was {gap:.3f} below the "
                    f"short's stop, within the {spread:.2f} spread")
    report.findings.append(Finding(record.date, 2, f"{live.side}.exit", live_exit, rep_exit,
                                   status, note))


def replay_r(trades: list[ReplayTrade], day: ReplayDay, risk: float) -> dict[ReplayTrade, float]:
    """R of each of a day's replay trades: net P&L / (risk x equity at entry), equity at entry =
    the plan's equity + earlier closed trades that day (the same rule as the live records)."""
    out = {}
    for t in sorted(trades, key=lambda t: t.entry_time):
        equity = day.equity + sum(o.net_pnl for o in out if o.exit_time <= t.entry_time)
        out[t] = t.net_pnl / (risk * equity)
    return out


def _layer3(report: Report, record: DayRecord, day: ReplayDay, replay: Replay) -> None:
    reps = [t for t in replay.trades if t.entry_time.date() == record.date]
    rs = replay_r(reps, day, replay.risk)
    for live in record.trades:
        rep = next((t for t in reps if t.side == live.side), None)
        if rep is None:
            continue
        report.execution.append(ExecutionRow(
            day=record.date, side=live.side, live_entry=live.entry_price,
            replay_entry=rep.entry_price, live_exit=live.exit_price, replay_exit=rep.exit_price,
            spread_at_entry=live.spread_at_entry, spread_at_exit=live.spread_at_exit,
            commission=live.commission, live_r=live.r, replay_r=rs[rep]))
