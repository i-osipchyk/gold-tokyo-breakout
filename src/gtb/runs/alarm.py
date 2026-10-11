"""Forward CloudWatch alarms (Lambda errors, via SNS) to Telegram."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from ..alerts.messages import alert_text
from .deps import Notifier


async def forward_alarms(event: dict[str, Any], notifier: Notifier, *, env: str) -> None:
    for record in event.get("Records", []):
        alarm = json.loads(record["Sns"]["Message"])
        if alarm.get("NewStateValue") != "ALARM":
            continue
        day = date.fromisoformat(alarm["StateChangeTime"][:10])
        await notifier.send(alert_text(
            env, day, f"AWS alarm {alarm['AlarmName']}: {alarm['NewStateReason']}"))
