"""config: demo|live settings, the early-close calendar, secrets from SSM (or .env locally)."""

from datetime import date, time
from pathlib import Path

import boto3
import pytest
from botocore.stub import Stubber

from gtb.config import load_config, load_secrets, ssm_parameters

CALENDAR = Path(__file__).parents[1] / "data" / "early_close.yaml"


def test_live_trades_a_quarter_percent_on_the_live_endpoint():
    config = load_config({"GTB_ENV": "live", "GTB_BUCKET": "gtb-records", "GIT_SHA": "abc123"},
                         calendar=CALENDAR)

    assert (config.env, config.risk, config.host) == ("live", 0.0025, "live.ctraderapi.com")
    assert (config.bucket, config.code_version, config.ssm_prefix) == (
        "gtb-records", "abc123", "/gtb/live/")


def test_demo_trades_one_percent_on_the_demo_endpoint_with_the_early_close_calendar():
    config = load_config({"GTB_ENV": "demo"}, calendar=CALENDAR)

    assert (config.risk, config.host, config.code_version) == (0.01, "demo.ctraderapi.com",
                                                               "unknown")
    assert config.early_closes[date(2026, 7, 3)] == time(16, 55)
    assert date(2026, 10, 12) not in config.early_closes


def test_an_unknown_environment_is_refused():
    with pytest.raises(ValueError, match="paper"):
        load_config({"GTB_ENV": "paper"}, calendar=CALENDAR)


def test_secrets_come_from_parameters_named_like_the_env_file_in_lower_case():
    secrets = load_secrets({
        "ctrader_client_id": "app", "ctrader_client_secret": "s3cret",
        "ctrader_access_token": "tok", "ctrader_refresh_token": "ref",
        "ctrader_account_id": "46585934", "telegram_bot_token": "1:abc",
        "telegram_chat_id": "-100"})

    assert (secrets.client_id, secrets.account_id, secrets.telegram_chat_id) == (
        "app", 46585934, "-100")
    assert "s3cret" not in repr(secrets) and "tok" not in repr(secrets)


def test_ssm_parameters_are_read_decrypted_by_path_and_keyed_by_short_name():
    client = boto3.client("ssm", region_name="eu-west-1", aws_access_key_id="x",
                          aws_secret_access_key="x")
    stub = Stubber(client)
    stub.add_response("get_parameters_by_path", {
        "Parameters": [{"Name": "/gtb/demo/ctrader_client_id", "Value": "app"}],
        "NextToken": "page2"},
        {"Path": "/gtb/demo/", "WithDecryption": True, "Recursive": False})
    stub.add_response("get_parameters_by_path", {
        "Parameters": [{"Name": "/gtb/demo/telegram_chat_id", "Value": "-100"}]},
        {"Path": "/gtb/demo/", "WithDecryption": True, "Recursive": False,
         "NextToken": "page2"})

    with stub:
        params = ssm_parameters(client, "/gtb/demo/")

    assert params == {"ctrader_client_id": "app", "telegram_chat_id": "-100"}
