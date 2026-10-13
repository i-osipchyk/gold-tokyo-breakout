"""Three-layer reconciliation report (ARCHITECTURE §4.5, IMPLEMENTATION_PLAN Phase 6).

    uv run python -m reconcile.report --env dev               # records from S3 (aws s3 sync)
    uv run python -m reconcile.report --env dev --local       # records from ./local (run_local)
        [--end YYYY-MM-DD] [--hermes-dir ../hermes]

Pulls the DayRecords, replays the Hermes static variant from before the first record (range-history
warm-up; Hermes adds the ATR lead-in) to --end with the account's equity, then compares.
The edge clock counts every replay trade since the rules were frozen; the execution clock every
trade both took. Exit code 1 if a layer-1/2 mismatch, a missing day or a clock breach is found.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import dotenv

from gtb.config import ACCOUNT, CALENDAR, RISK, load_early_closes
from gtb.records import DayRecord

from .clocks import edge_clock, execution_clock
from .compare import Report, compare, replay_r
from .replay import Replay

ROOT = Path(__file__).parents[1]
FREEZE = date(2026, 10, 11)       # docs/REFERENCE.md: rules frozen, edge clock starts
WARM_UP = timedelta(days=45)      # >= 20 previous valid Tokyo ranges


def sync(env: str) -> Path:
    account = subprocess.run(["aws", "sts", "get-caller-identity", "--query", "Account",
                              "--output", "text"], check=True, capture_output=True,
                             text=True).stdout.strip()
    target = ROOT / "reconcile" / "data"
    region = os.environ.get("AWS_REGION", "eu-west-1")
    subprocess.run(["aws", "s3", "sync", "--region", region, "--only-show-errors",
                    f"s3://gtb-{env}-records-{account}/{env}/records/",
                    str(target / env / "records")], check=True)
    return target


def load_records(root: Path, env: str) -> list[DayRecord]:
    return sorted((DayRecord.from_json(p.read_text())
                   for p in (root / env / "records").glob("*/*/*.json")),
                  key=lambda r: r.date)


def run_replay(hermes_dir: Path, start: date, end: date, equity: float) -> Replay:
    early = {d.isoformat(): t.strftime("%H:%M") for d, t in load_early_closes(CALENDAR).items()}
    with tempfile.TemporaryDirectory() as tmp:
        request, out = Path(tmp) / "request.json", Path(tmp) / "replay.json"
        request.write_text(json.dumps({"start": start.isoformat(), "end": end.isoformat(),
                                       "equity": equity, "early_closes": early}))
        subprocess.run([str(hermes_dir / ".venv" / "bin" / "python"),
                        str(ROOT / "reconcile" / "hermes_replay.py"), str(request), str(out)],
                       cwd=hermes_dir, check=True)
        return Replay.from_json(out.read_text())


def _fmt(x: object) -> str:
    if isinstance(x, float):
        return f"{x:.4f}".rstrip("0").rstrip(".")
    if isinstance(x, tuple):
        return "(" + ", ".join(_fmt(v) for v in x) + ")"
    return "—" if x is None else str(getattr(x, "isoformat", lambda: x)())


def print_report(report: Report, replay: Replay, env: str) -> bool:
    """Print it; True if anything needs a person (mismatch, missing day, clock breach)."""
    bad = False
    for layer, title in ((1, "inputs"), (2, "orders and triggers")):
        found = [f for f in report.findings if f.layer == layer]
        counts = Counter(f.status for f in found)
        print(f"\nLayer {layer} — {title}: " +
              ", ".join(f"{n} {s}" for s, n in sorted(counts.items())))
        for f in found:
            if f.status != "ok":
                bad |= f.status in ("mismatch", "missing")
                print(f"  {f.day} {f.field:<14} {f.status:<9} live {_fmt(f.live)}  "
                      f"replay {_fmt(f.replay)}  {f.note}")

    print("\nLayer 3 — execution (live − replay R per trade)")
    for r in report.execution:
        diff = "—" if r.r_diff is None else f"{r.r_diff:+.3f}"
        print(f"  {r.day} {r.side:<4} entry {r.live_entry} vs {r.replay_entry:.3f}  exit "
              f"{_fmt(r.live_exit)} vs {r.replay_exit:.3f}  spread {_fmt(r.spread_at_entry)}/"
              f"{_fmt(r.spread_at_exit)}  comm {r.commission:.2f}  R {_fmt(r.live_r)} vs "
              f"{r.replay_r:.3f}  Δ {diff}")
    if not report.execution:
        print("  no trades taken by both")

    edge_rs = []
    for d, day in sorted(replay.days.items()):
        if d >= FREEZE:
            trades = [t for t in replay.trades if t.entry_time.date() == d]
            edge_rs += [r for _, r in sorted(
                replay_r(trades, day, replay.risk).items(), key=lambda kv: kv[0].entry_time)]
    edge = edge_clock(edge_rs)
    mean = "—" if edge.mean_r is None else f"{edge.mean_r:+.3f}"
    print(f"\nEdge clock (replay since {FREEZE}): {edge.trades} trades, {edge.total_r:+.2f} R, "
          f"drawdown {edge.drawdown_r:.2f} R (limit −47.2), mean {mean} R")
    diffs = [r.r_diff for r in report.execution if r.r_diff is not None]
    ex = execution_clock(diffs)
    rolling = "—" if ex.rolling_mean is None else f"{ex.rolling_mean:+.3f}"
    print(f"Execution clock ({env}): {ex.trades} trades, rolling-20 mean Δ {rolling} R "
          f"(limit −0.05)" + ("" if env == "prod" else f"  [{env}: not a kill rule]"))
    for name, breached in (("DRAWDOWN", edge.drawdown_breached),
                           ("EXPECTANCY", edge.expectancy_breached),
                           ("EXECUTION", ex.breached and env == "prod")):
        if breached:
            bad = True
            print(f"!! {name} kill rule breached")
    return bad


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=sorted(ACCOUNT), required=True)
    parser.add_argument("--local", action="store_true", help="records from ./local (run_local)")
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--hermes-dir", type=Path,
                        default=Path(os.environ.get("HERMES_DIR", ROOT.parent / "hermes")))
    args = parser.parse_args()
    dotenv.load_dotenv(ROOT / ".env")             # AWS_REGION, if not exported

    root = ROOT / "local" if args.local else sync(args.env)
    records = [r for r in load_records(root, args.env) if r.inputs is not None]
    if not records:
        sys.exit(f"no records under {root / args.env / 'records'}")
    first, end = records[0], args.end or records[-1].date
    assert first.inputs is not None
    replay = run_replay(args.hermes_dir.resolve(), min(FREEZE, first.date) - WARM_UP, end,
                        first.inputs.equity)
    print(f"{args.env}: {len(records)} records {first.date} -> {records[-1].date}; "
          f"replay to {end}")
    report = compare(records, replay, risk=RISK[ACCOUNT[args.env]])
    sys.exit(1 if print_report(report, replay, args.env) else 0)


if __name__ == "__main__":
    main()
