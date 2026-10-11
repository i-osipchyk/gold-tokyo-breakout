"""Push the run secrets from .env to SSM Parameter Store as SecureStrings under /gtb/<env>/
(IMPLEMENTATION_PLAN Phase 5). Terraform never holds them.

    uv run python scripts/put_secrets.py --env demo [--dry-run]

Reads CTRADER_CLIENT_ID, CTRADER_CLIENT_SECRET, CTRADER_ACCESS_TOKEN, CTRADER_REFRESH_TOKEN,
CTRADER_ACCOUNT_ID, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID. Prints names only, never values. Also
stamps ctrader_token_refreshed_at = now, so the heartbeat's first refresh is a week out; after it
rotates the pair, the copy in .env is stale (fetch the new one from SSM for local runs).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3
import dotenv

NAMES = ("ctrader_client_id", "ctrader_client_secret", "ctrader_access_token",
         "ctrader_refresh_token", "ctrader_account_id", "telegram_bot_token", "telegram_chat_id")
REGION = "eu-central-1"


def main() -> None:
    dotenv.load_dotenv(Path(__file__).parents[1] / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", choices=("demo", "live"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    values = {n: os.environ.get(n.upper(), "") for n in NAMES}
    missing = [n.upper() for n, v in values.items() if not v]
    if missing:
        sys.exit(f"missing in .env: {', '.join(missing)}")
    prefix = f"/gtb/{args.env}/"
    ssm = boto3.client("ssm", region_name=REGION)
    for name, value in values.items():
        print(f"{'would put' if args.dry_run else 'put'} {prefix}{name} (SecureString)")
        if not args.dry_run:
            ssm.put_parameter(Name=prefix + name, Value=value, Type="SecureString",
                              Overwrite=True)
    stamp = datetime.now(UTC).isoformat()
    print(f"{'would put' if args.dry_run else 'put'} {prefix}ctrader_token_refreshed_at = {stamp}")
    if not args.dry_run:
        ssm.put_parameter(Name=prefix + "ctrader_token_refreshed_at", Value=stamp, Type="String",
                          Overwrite=True)


if __name__ == "__main__":
    main()
