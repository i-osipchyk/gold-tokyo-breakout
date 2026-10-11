"""What the Hermes replay hands the reconciler (written by reconcile/hermes_replay.py)."""

from __future__ import annotations

import json
from dataclasses import dataclass, fields
from datetime import date, datetime
from typing import Any

from gtb.core import Bar


@dataclass(frozen=True)
class ReplayDay:
    """The variant's plan for one weekday (its ``day_plans`` row)."""

    date: date
    tokyo_high: float | None
    tokyo_low: float | None
    bars: int
    range: float | None
    history_len: int
    median: float | None
    atr: float | None
    equity: float
    trade: bool
    reason: str | None
    long_entry: float | None
    long_sl: float | None
    short_entry: float | None
    short_sl: float | None
    d: float | None
    shortened: bool | None
    lots: float | None


@dataclass(frozen=True)
class ReplayTrade:
    entry_time: datetime          # open of the 5m bar the entry filled on
    side: str                     # "buy" | "sell"
    entry_price: float
    stop_loss: float
    lots: float
    exit_time: datetime
    exit_price: float
    exit_reason: str              # Hermes: "stop_loss" | "signal" (the flatten)
    net_pnl: float


@dataclass
class Replay:
    days: dict[date, ReplayDay]
    trades: list[ReplayTrade]
    bars: list[Bar]               # 5m bid bars of the window days, for trigger classification
    risk: float                   # the variant's risk per trade (its RISK constant)

    @classmethod
    def from_json(cls, text: str) -> Replay:
        doc = json.loads(text)
        day_fields = {f.name for f in fields(ReplayDay)}

        def day(d: dict[str, Any]) -> ReplayDay:
            d = {k: v for k, v in d.items() if k in day_fields}
            d["date"] = date.fromisoformat(d["date"])
            return ReplayDay(**d)

        def trade(t: dict[str, Any]) -> ReplayTrade:
            t = dict(t)
            t["entry_time"] = datetime.fromisoformat(t["entry_time"])
            t["exit_time"] = datetime.fromisoformat(t["exit_time"])
            return ReplayTrade(**t)

        days = [day(d) for d in doc["days"]]
        return cls(days={d.date: d for d in days}, trades=[trade(t) for t in doc["trades"]],
                   bars=[Bar(datetime.fromisoformat(b[0]), *b[1:]) for b in doc["bars"]],
                   risk=doc["risk"])
