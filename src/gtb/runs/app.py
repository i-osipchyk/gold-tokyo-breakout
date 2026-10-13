"""Wiring: real clock, cTrader client, record store and notifier around the run handlers, for the
Lambda entry points and ``scripts/run_local.py``. Any run error is alerted, then re-raised (so
the Lambda error alarm fires too)."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from ..alerts import TelegramNotifier
from ..alerts.messages import alert_text
from ..broker import CTraderClient
from ..config import SHARED_PREFIX, Config, Secrets, load_config, load_secrets, ssm_parameters
from ..records import RecordStore, S3RecordStore
from .deps import Deps, Notifier
from .eod import eod
from .heartbeat import heartbeat
from .morning import morning
from .watchdog import watchdog


class SystemClock:
    """Wall-clock UTC, optionally shifted by ``offset`` (local runs pretending to be at 09:00)."""

    def __init__(self, offset: timedelta = timedelta(0)) -> None:
        self.offset = offset

    def now(self) -> datetime:
        return datetime.now(UTC) + self.offset

    async def sleep_until(self, t: datetime) -> None:
        await asyncio.sleep(max(0.0, (t - self.now()).total_seconds()))


class PrintNotifier:
    async def send(self, text: str) -> None:
        print(f"--- message ---\n{text}\n---------------")


RUNS = ("morning", "heartbeat", "eod", "watchdog")


async def run(name: str, config: Config, secrets: Secrets, *, store: RecordStore,
              notifier: Notifier, clock: SystemClock | None = None) -> None:
    """Connect, check the account is the kind ``config.env`` trades, run handler ``name``."""
    clock = clock or SystemClock()
    try:
        async with CTraderClient(config.host, client_id=secrets.client_id,
                                 client_secret=secrets.client_secret,
                                 access_token=secrets.access_token,
                                 account_id=secrets.account_id) as client:
            if await client.is_live() != (config.account == "live"):
                raise RuntimeError(f"account {secrets.account_id} is not a {config.account} "
                                   f"account, which {config.env} needs; refusing to run")
            deps = Deps(env=config.env, risk=config.risk, broker=client, store=store,
                        notifier=notifier, clock=clock, early_closes=config.early_closes,
                        code_version=config.code_version)
            handlers: Mapping[str, Callable[[], Awaitable[Any]]] = {
                "morning": lambda: morning(deps),
                "eod": lambda: eod(deps),
                "watchdog": lambda: watchdog(deps),
                "heartbeat": lambda: heartbeat(deps),
            }
            await handlers[name]()
    except Exception as e:
        await notifier.send(alert_text(config.env, clock.now().date(),
                                       f"{name} run failed: {type(e).__name__}: {e}"))
        raise


# --- Lambda entry points (one image; each function names its handler) ----------------------

def _lambda(name: str) -> None:
    import boto3

    config = load_config(os.environ)
    ssm = boto3.client("ssm")
    shared = os.environ.get("SHARED_SECRETS_PATH", SHARED_PREFIX).rstrip("/") + "/"
    secrets = load_secrets(ssm_parameters(ssm, config.ssm_prefix), ssm_parameters(ssm, shared))
    if config.bucket is None:
        raise RuntimeError("GTB_BUCKET is not set")
    store = S3RecordStore(boto3.client("s3"), config.bucket, config.env)
    notifier = TelegramNotifier(secrets.telegram_bot_token, secrets.telegram_chat_id)
    asyncio.run(run(name, config, secrets, store=store, notifier=notifier))


def morning_handler(event: object, context: object) -> None:
    _lambda("morning")


def heartbeat_handler(event: object, context: object) -> None:
    _lambda("heartbeat")


def eod_handler(event: object, context: object) -> None:
    _lambda("eod")


def watchdog_handler(event: object, context: object) -> None:
    _lambda("watchdog")

