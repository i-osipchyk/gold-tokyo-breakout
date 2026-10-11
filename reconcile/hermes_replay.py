"""Replay the Hermes static variant for the reconciler. Runs in HERMES' venv, from HERMES_DIR:

    cd $HERMES_DIR && .venv/bin/python <this file> request.json replay.json

request.json: {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "equity": float,
               "early_closes": {"YYYY-MM-DD": "HH:MM", ...}}
replay.json:  {"risk", "days": [day_plans rows], "trades": [...], "bars": [[ts, o, h, l, c]]}

Imports only Hermes and the variant (the bot is never imported here, and nothing here is shipped).
``start`` should already include the warm-up the range history needs; Hermes adds the ATR lead-in
itself. Bars are the 5m bid bars from 09:00 to 21:00 of each weekday, for classifying triggers.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, time
from pathlib import Path

import dotenv

dotenv.load_dotenv(".env")

_spec = importlib.util.spec_from_file_location(
    "static", Path("strategies/misc/gold_tokyo_breakout_v2_static.py"))
assert _spec is not None and _spec.loader is not None
static = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(static)


def _iso(x: object) -> object:
    return x.isoformat() if isinstance(x, (date, datetime)) else x


def main(request_path: str, out_path: str) -> None:
    req = json.loads(Path(request_path).read_text())
    start = datetime.combine(date.fromisoformat(req["start"]), time(0), tzinfo=UTC)
    end = datetime.combine(date.fromisoformat(req["end"]), time(23, 59), tzinfo=UTC)
    early = {date.fromisoformat(d): time.fromisoformat(t) for d, t in req["early_closes"].items()}
    differs = {d for d in static.EARLY_CLOSES if early.get(d) != static.EARLY_CLOSES[d]}
    if differs:
        print(f"warning: early_close.yaml disagrees with the variant on {sorted(differs)}",
              file=sys.stderr)

    strategy = static.GoldTokyoBreakoutV2Static(early_closes={**static.EARLY_CLOSES, **early})
    bt = static.build_backtest(strategy=strategy, params={"width_ratio": 1.0}, start=start,
                               end=end, starting_cash=req["equity"])
    result = bt.run()

    from hermes import Symbol, Timeframe
    from hermes.data import PepperstoneSource

    source = PepperstoneSource()
    inst = source.get_instrument(Symbol("XAUUSD", "pepperstone"))
    bars = [[b.timestamp.astimezone(UTC).isoformat(), b.open, b.high, b.low, b.close]
            for b in source.history(inst, Timeframe.parse("5m"), start, end)
            if b.timestamp.weekday() < 5 and 9 <= b.timestamp.astimezone(UTC).hour < 21]

    doc = {
        "risk": static.RISK,
        "days": [{k: _iso(v) for k, v in p.items()} for p in strategy.day_plans],
        "trades": [{
            "entry_time": t.entry_time.astimezone(UTC).isoformat(),
            "side": "buy" if t.side.name == "BUY" else "sell",
            "entry_price": t.entry_price, "stop_loss": t.stop_loss, "lots": t.size,
            "exit_time": t.exit_time.astimezone(UTC).isoformat(), "exit_price": t.exit_price,
            "exit_reason": t.exit_reason, "net_pnl": t.net_pnl,
        } for t in result.trades],
        "bars": bars,
    }
    Path(out_path).write_text(json.dumps(doc))
    print(f"replay {start:%Y-%m-%d} -> {end:%Y-%m-%d}: {len(doc['days'])} day plans, "
          f"{len(doc['trades'])} trades, {len(bars)} bars", file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
