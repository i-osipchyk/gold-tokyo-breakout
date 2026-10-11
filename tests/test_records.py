"""DayRecord JSON (ARCHITECTURE §4.4) and its S3 store."""

import io
import json
from datetime import UTC, date, datetime

import boto3
from botocore.response import StreamingBody
from botocore.stub import ANY, Stubber

from gtb.records import (
    DayRecord,
    Decision,
    Inputs,
    OrderRecord,
    S3RecordStore,
    TradeRecord,
)

DAY = date(2026, 10, 12)


def _at(h, m, s=0):
    return datetime(2026, 10, 12, h, m, s, tzinfo=UTC)


def _record():
    return DayRecord(
        date=DAY, env="demo", code_version="abc123",
        inputs=Inputs(tokyo_high=4012.5, tokyo_low=3990.1, bars=108, range=22.4, history_len=20,
                      median=18.0, atr=37.25, equity=100_000.0, plan_time=_at(9, 0, 14),
                      attempts=1),
        decision=Decision(trade=True, reason=None),
        orders=[OrderRecord(side="buy", entry=4013.25, sl=3976.0, d=37.25, shortened=False,
                            lots=0.26, expiry=_at(20, 5), client_order_id="gtb-20261012-L",
                            status="filled", order_id=7)],
        trades=[TradeRecord(side="buy", entry_time=_at(9, 41, 2), entry_price=4013.31,
                            spread_at_entry=0.18, exit_time=_at(20, 50, 6), exit_price=4040.0,
                            spread_at_exit=0.22, exit_reason="eod", lots=0.26, commission=-1.82,
                            pnl=692.12, risk_usd=1000.0, r=0.69212, position_id=9)],
        alerts=["something"])


def test_a_record_survives_a_json_round_trip_in_the_documented_shape():
    text = _record().to_json()

    assert DayRecord.from_json(text) == _record()
    doc = json.loads(text)
    assert doc["date"] == "2026-10-12"
    assert doc["inputs"]["plan_time"] == "2026-10-12T09:00:14Z"
    assert doc["orders"][0]["expiry"] == "2026-10-12T20:05:00Z"
    assert set(doc["inputs"]) == {"tokyo_high", "tokyo_low", "bars", "range", "history_len",
                                  "median", "atr", "equity", "plan_time", "attempts"}
    assert {"side", "entry_time", "entry_price", "spread_at_entry", "exit_time", "exit_price",
            "spread_at_exit", "exit_reason", "lots", "commission", "pnl", "risk_usd",
            "r"} <= set(doc["trades"][0])


def _s3():
    client = boto3.client("s3", region_name="eu-central-1", aws_access_key_id="x",
                          aws_secret_access_key="x")
    return client, Stubber(client)


def test_the_s3_store_reads_and_writes_under_env_records_yyyy_mm_dd():
    client, stub = _s3()
    text = _record().to_json()
    stub.add_response("put_object", {}, {
        "Bucket": "gtb-bucket", "Key": "demo/records/2026/10/12.json", "Body": ANY,
        "ContentType": "application/json"})
    body = StreamingBody(io.BytesIO(text.encode()), len(text.encode()))
    stub.add_response("get_object", {"Body": body},
                      {"Bucket": "gtb-bucket", "Key": "demo/records/2026/10/12.json"})
    store = S3RecordStore(client, "gtb-bucket", "demo")

    with stub:
        store.put(_record())
        assert store.get(DAY) == _record()
    stub.assert_no_pending_responses()


def test_the_s3_store_returns_none_for_a_day_without_a_record():
    client, stub = _s3()
    stub.add_client_error("get_object", service_error_code="NoSuchKey", http_status_code=404)

    with stub:
        assert S3RecordStore(client, "gtb-bucket", "demo").get(DAY) is None
