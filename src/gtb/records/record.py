"""DayRecord: one JSON document per trading day (ARCHITECTURE §4.4), written in two parts
(morning, eod). Execution facts come from the broker's deals and ticks."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields
from datetime import UTC, date, datetime
from typing import Any


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _dt(s: str | None) -> datetime | None:
    return None if s is None else datetime.fromisoformat(s.replace("Z", "+00:00"))


@dataclass
class Inputs:
    tokyo_high: float | None
    tokyo_low: float | None
    bars: int
    range: float | None
    history_len: int
    median: float | None
    atr: float | None
    equity: float
    plan_time: datetime | None
    attempts: int


@dataclass
class Decision:
    trade: bool
    reason: str | None


@dataclass
class OrderRecord:
    side: str
    entry: float
    sl: float
    d: float
    shortened: bool
    lots: float
    expiry: datetime
    client_order_id: str
    status: str                   # placed | rejected | filled | expired | cancelled
    order_id: int | None = None


@dataclass
class TradeRecord:
    side: str
    entry_time: datetime
    entry_price: float
    spread_at_entry: float | None
    exit_time: datetime | None
    exit_price: float | None
    spread_at_exit: float | None
    exit_reason: str | None       # sl | eod | watchdog
    lots: float
    commission: float
    pnl: float | None             # net: gross P&L + commission
    risk_usd: float
    r: float | None
    position_id: int | None = None


_TIMES = {"plan_time", "expiry", "entry_time", "exit_time"}


def _dump(obj: Any) -> dict[str, Any]:
    return {k: _iso(v) if k in _TIMES else v for k, v in asdict(obj).items()}


def _load[T](cls: type[T], d: dict[str, Any]) -> T:
    names = {f.name for f in fields(cls)}  # type: ignore[arg-type]
    return cls(**{k: _dt(v) if k in _TIMES else v for k, v in d.items() if k in names})


@dataclass
class DayRecord:
    date: date
    env: str
    code_version: str
    inputs: Inputs | None = None
    decision: Decision | None = None
    orders: list[OrderRecord] = field(default_factory=list)
    trades: list[TradeRecord] = field(default_factory=list)
    alerts: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps({
            "date": self.date.isoformat(), "env": self.env, "code_version": self.code_version,
            "inputs": None if self.inputs is None else _dump(self.inputs),
            "decision": None if self.decision is None else asdict(self.decision),
            "orders": [_dump(o) for o in self.orders],
            "trades": [_dump(t) for t in self.trades],
            "alerts": list(self.alerts),
        }, indent=2)

    @classmethod
    def from_json(cls, text: str) -> DayRecord:
        d = json.loads(text)
        return cls(
            date=date.fromisoformat(d["date"]), env=d["env"], code_version=d["code_version"],
            inputs=None if d.get("inputs") is None else _load(Inputs, d["inputs"]),
            decision=None if d.get("decision") is None else Decision(**d["decision"]),
            orders=[_load(OrderRecord, o) for o in d.get("orders", [])],
            trades=[_load(TradeRecord, t) for t in d.get("trades", [])],
            alerts=list(d.get("alerts", [])))
