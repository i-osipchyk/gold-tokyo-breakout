"""Sending to Telegram through the Bot API."""

from __future__ import annotations

import httpx

API = "https://api.telegram.org"
MAX_LEN = 4096                            # Telegram's limit per message


class TelegramNotifier:
    def __init__(self, bot_token: str, chat_id: str, *, http: httpx.AsyncClient | None = None,
                 timeout: float = 10) -> None:
        self._url = f"{API}/bot{bot_token}/sendMessage"
        self.chat_id = chat_id
        self._http = http
        self.timeout = timeout

    async def send(self, text: str) -> None:
        payload = {"chat_id": self.chat_id, "text": text[:MAX_LEN]}
        if self._http is not None:
            res = await self._http.post(self._url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=self.timeout) as http:
                res = await http.post(self._url, json=payload)
        body = res.json() if res.headers.get("content-type", "").startswith("application/json") \
            else {}
        if res.status_code != 200 or not body.get("ok"):
            # the URL holds the bot token: never put it in the error
            raise RuntimeError(f"Telegram sendMessage failed ({res.status_code}): "
                               f"{body.get('description', res.text[:200])}")
