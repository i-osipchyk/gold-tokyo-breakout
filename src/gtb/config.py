"""Run configuration (ARCHITECTURE §5): dev|prod settings from the environment, secrets from SSM
Parameter Store (``/gtb/<env>/<name>``) -- or, locally, from .env under the same names in upper
case (``CTRADER_CLIENT_ID`` <-> ``ctrader_client_id``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, time
from pathlib import Path
from typing import Any

import yaml

from .broker import DEMO_HOST, LIVE_HOST

# Deployment env -> the kind of cTrader account it trades: dev on demo, prod on live.
ACCOUNT = {"dev": "demo", "prod": "live"}
RISK = {"demo": 0.01, "live": 0.0025}           # ARCHITECTURE §2, §8 -- frozen
HOST = {"demo": DEMO_HOST, "live": LIVE_HOST}
CALENDAR = Path(__file__).parents[2] / "data" / "early_close.yaml"


@dataclass(frozen=True)
class Config:
    env: str
    risk: float
    host: str
    bucket: str | None
    code_version: str
    early_closes: Mapping[date, time]

    @property
    def account(self) -> str:
        """``demo`` | ``live``: the account kind this env must run on."""
        return ACCOUNT[self.env]

    @property
    def ssm_prefix(self) -> str:
        return f"/gtb/{self.env}/"


def load_early_closes(path: Path, sections: tuple[str, ...] = ("past", "future")
                      ) -> dict[date, time]:
    """``data/early_close.yaml``: past and future dates -> open time (UTC) of the last 5m bar."""
    doc = yaml.safe_load(path.read_text())
    out = {}
    for section in sections:
        for day, last_bar in (doc.get(section) or {}).items():
            out[date.fromisoformat(str(day))] = time.fromisoformat(str(last_bar))
    return out


def load_config(environ: Mapping[str, str], *, calendar: Path | None = None) -> Config:
    env = environ.get("GTB_ENV", "")
    if env not in ACCOUNT:
        raise ValueError(f"GTB_ENV must be dev or prod, not {env!r}")
    account = ACCOUNT[env]
    return Config(env=env, risk=RISK[account], host=HOST[account], bucket=environ.get("GTB_BUCKET"),
                  code_version=environ.get("GIT_SHA", "unknown"),
                  early_closes=load_early_closes(
                      calendar or Path(environ.get("GTB_CALENDAR", CALENDAR))))


@dataclass(frozen=True)
class Secrets:
    client_id: str
    client_secret: str = field(repr=False)
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    account_id: int
    telegram_bot_token: str = field(repr=False)
    telegram_chat_id: str


def load_secrets(params: Mapping[str, str]) -> Secrets:
    return Secrets(
        client_id=params["ctrader_client_id"], client_secret=params["ctrader_client_secret"],
        access_token=params["ctrader_access_token"],
        refresh_token=params.get("ctrader_refresh_token", ""),
        account_id=int(params["ctrader_account_id"]),
        telegram_bot_token=params.get("telegram_bot_token", ""),
        telegram_chat_id=params.get("telegram_chat_id", ""))


def ssm_parameters(client: Any, prefix: str) -> dict[str, str]:
    """Every parameter directly under ``prefix``, decrypted, keyed by its last path segment."""
    out: dict[str, str] = {}
    kwargs: dict[str, Any] = {"Path": prefix, "WithDecryption": True, "Recursive": False}
    while True:
        res = client.get_parameters_by_path(**kwargs)
        for p in res["Parameters"]:
            out[p["Name"].rsplit("/", 1)[-1]] = p["Value"]
        if not res.get("NextToken"):
            return out
        kwargs["NextToken"] = res["NextToken"]
