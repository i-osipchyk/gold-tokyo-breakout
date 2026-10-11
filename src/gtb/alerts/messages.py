"""Telegram message texts. Plain text; every number the reconciler's layer 1 needs is in the
morning summary so a day can be checked from the phone."""

from __future__ import annotations

from datetime import date

from ..records import DayRecord


def _num(x: float | None, digits: int = 2) -> str:
    return "—" if x is None else f"{x:,.{digits}f}"


def alert_text(env: str, day: date, text: str) -> str:
    return f"ALERT [{env}] {day}: {text}"


def alert(record: DayRecord, text: str) -> str:
    return alert_text(record.env, record.date, text)


def morning_summary(record: DayRecord) -> str:
    i, d = record.inputs, record.decision
    assert i is not None and d is not None
    lines = [
        f"[{record.env}] {record.date} morning",
        f"Tokyo {_num(i.tokyo_low)}–{_num(i.tokyo_high)} ({i.bars} bars), "
        f"range {_num(i.range)} vs median {_num(i.median)} ({i.history_len})",
        f"ATR {_num(i.atr, 3)}, equity {_num(i.equity)}",
        "TRADE" if d.trade else f"no trade: {d.reason}",
    ]
    for o in record.orders:
        lines.append(f"{o.side} stop {o.entry} SL {o.sl} ({'short ' if o.shortened else ''}"
                     f"d {_num(o.d, 3)}) {o.lots} lot — {o.status}")
    return "\n".join(lines)


def eod_summary(record: DayRecord) -> str:
    lines = [f"[{record.env}] {record.date} eod"]
    for o in record.orders:
        lines.append(f"{o.side} stop {o.entry}: {o.status}")
    for t in record.trades:
        if t.pnl is None:
            lines.append(f"{t.side} {t.lots} lot @ {t.entry_price} STILL OPEN")
            continue
        lines.append(f"{t.side} {t.lots} lot {t.entry_price} → {t.exit_price} ({t.exit_reason}): "
                     f"{_num(t.pnl)} = {_num(t.r)} R")
    closed = [t for t in record.trades if t.pnl is not None and t.r is not None]
    if closed:
        lines.append(f"day: {_num(sum(t.pnl or 0 for t in closed))} = "
                     f"{_num(sum(t.r or 0 for t in closed))} R")
    elif not record.trades:
        lines.append("no trades")
    return "\n".join(lines)
