"""TelegramNotifier: messages go to the Bot API's sendMessage (httpx MockTransport, offline)."""

import asyncio
import json

import httpx
import pytest

from gtb.alerts import TelegramNotifier


def _send(handler, text):
    async def body():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            await TelegramNotifier("123:secret", "-1001", http=http).send(text)
    asyncio.run(body())


def test_a_message_is_posted_to_the_chat():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {}})

    _send(handler, "hello")

    (r,) = requests
    assert (r.method, str(r.url)) == ("POST", "https://api.telegram.org/bot123:secret/sendMessage")
    assert json.loads(r.content) == {"chat_id": "-1001", "text": "hello"}


def test_a_refused_message_raises_without_leaking_the_token():
    def handler(request):
        return httpx.Response(400, json={"ok": False, "description": "Bad Request: chat not found"})

    with pytest.raises(RuntimeError) as e:
        _send(handler, "hello")

    assert "chat not found" in str(e.value) and "secret" not in str(e.value)
