"""refresh_tokens: renew the Open API access token and persist it through a TokenStore."""

import asyncio

from ctrader_open_api.messages import OpenApiMessages_pb2 as M

from gtb.broker import Tokens, refresh_tokens

from fake_ctrader import FakeCTrader


class MemoryStore:
    def __init__(self, tokens):
        self.tokens = tokens
        self.saved = []

    def load(self):
        return self.tokens

    def save(self, tokens):
        self.saved.append(tokens)
        self.tokens = tokens


def test_refresh_swaps_the_refresh_token_for_new_tokens_and_saves_them():
    fake = FakeCTrader()
    fake.on(M.ProtoOARefreshTokenReq, lambda r: [M.ProtoOARefreshTokenRes(
        accessToken="token-2", refreshToken="refresh-2", tokenType="bearer",
        expiresIn=2_628_000)])
    store = MemoryStore(Tokens(access_token="token-1", refresh_token="refresh-1"))

    async def body():
        port = await fake.start()
        try:
            return await refresh_tokens("127.0.0.1", port, client_id="app-id",
                                        client_secret="app-secret", store=store, use_tls=False)
        finally:
            await fake.stop()

    tokens = asyncio.run(body())

    assert tokens == store.tokens == Tokens(access_token="token-2", refresh_token="refresh-2")
    assert store.saved == [tokens]
    assert [r.refreshToken for r in fake.of_type(M.ProtoOARefreshTokenReq)] == ["refresh-1"]
    assert fake.of_type(M.ProtoOAAccountAuthReq) == []     # works with an expired access token
