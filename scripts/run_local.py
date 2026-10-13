"""Run one handler locally against the real Open API (IMPLEMENTATION_PLAN Phase 4).

    uv run python scripts/run_local.py <morning|eod|watchdog|heartbeat> --env demo [--at HH:MM]

The cTrader credentials and token are the shared pair in SSM (/platform/shared/ctrader-*, read
with your AWS credentials; AWS_REGION, default eu-west-1); CTRADER_ACCOUNT_ID and optional
TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID come from .env. Records go to
./local/<env>/records/YYYY/MM/DD.json; messages go to Telegram when .env has its token, otherwise
they are printed. ``--at`` shifts the run's clock so it behaves as if started at that UTC time
today (e.g. ``eod --at 20:50`` to flatten now instead of waiting); waits inside the run are real.
Refuses to run when the account's live/demo status doesn't match ``--env``; the token is never
refreshed from here (only the platform's refresher does that).
"""

from __future__ import annotations

import argparse
import asyncio
import os
from datetime import UTC, datetime, time
from pathlib import Path

import boto3
import dotenv

from gtb.alerts import TelegramNotifier
from gtb.config import SHARED_PREFIX, load_config, load_secrets, ssm_parameters
from gtb.records import DirRecordStore
from gtb.runs.app import RUNS, PrintNotifier, SystemClock, run

ROOT = Path(__file__).parents[1]


def main() -> None:
    dotenv.load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("handler", choices=RUNS)
    parser.add_argument("--env", choices=("dev", "prod"), required=True)
    parser.add_argument("--at", type=time.fromisoformat, metavar="HH:MM[:SS]",
                        help="pretend the run started at this UTC time today")
    args = parser.parse_args()
    if args.env == "prod":
        raise SystemExit("prod (the live account) runs only from AWS (CLAUDE.md: no live trading "
                         "without an explicit instruction); use --env dev")

    config = load_config({"GTB_ENV": args.env, "GIT_SHA": "local"})
    os.environ.setdefault("AWS_REGION", "eu-west-1")
    secrets = load_secrets({k.lower(): v for k, v in os.environ.items()
                            if k.startswith(("CTRADER_", "TELEGRAM_"))},
                           ssm_parameters(boto3.client("ssm"), SHARED_PREFIX))
    clock = SystemClock()
    if args.at is not None:
        now = datetime.now(UTC)
        clock = SystemClock(datetime.combine(now.date(), args.at, tzinfo=UTC) - now)
    notifier = TelegramNotifier(secrets.telegram_bot_token, secrets.telegram_chat_id) \
        if secrets.telegram_bot_token and secrets.telegram_chat_id else PrintNotifier()
    store = DirRecordStore(ROOT / "local", args.env)
    print(f"{args.handler} on {args.env} (account {secrets.account_id}), clock {clock.now():%c}; "
          f"records under {store.root / args.env}")
    asyncio.run(run(args.handler, config, secrets, store=store, notifier=notifier, clock=clock))
    record = store.get(clock.now().date())
    if record is not None:
        print(record.to_json())


if __name__ == "__main__":
    main()
