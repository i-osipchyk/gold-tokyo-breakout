"""Lambda error alarms (CloudWatch -> SNS) are forwarded to Telegram, so every alert lands in
one place, including failures a run couldn't report itself (timeouts, crashes at start-up)."""

import asyncio
import json

from gtb.runs.alarm import forward_alarms

from fake_broker import FakeNotifier


def _sns(state, name="gtb-demo-eod-errors", reason="Threshold Crossed: 1 datapoint [1.0]"):
    return {"Records": [{"EventSource": "aws:sns", "Sns": {"Message": json.dumps({
        "AlarmName": name, "NewStateValue": state, "NewStateReason": reason,
        "StateChangeTime": "2026-10-12T20:56:12.345+0000"})}}]}


def test_an_alarm_is_forwarded_as_one_alert():
    notifier = FakeNotifier()

    asyncio.run(forward_alarms(_sns("ALARM"), notifier, env="demo"))

    (message,) = notifier.sent
    assert message.startswith("ALERT [demo] 2026-10-12")
    assert "gtb-demo-eod-errors" in message and "Threshold Crossed" in message


def test_a_return_to_ok_is_not_forwarded():
    notifier = FakeNotifier()

    asyncio.run(forward_alarms(_sns("OK"), notifier, env="demo"))

    assert notifier.sent == []
