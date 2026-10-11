"""Open API access tokens expire (~30 days); the bot renews them itself (ARCHITECTURE §5)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ctrader_open_api.messages import OpenApiMessages_pb2 as M

from .client import PORT, CTraderClient


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str


class TokenStore(Protocol):
    """Where the current tokens live (SSM Parameter Store in AWS)."""

    def load(self) -> Tokens: ...

    def save(self, tokens: Tokens) -> None: ...


async def refresh_tokens(host: str, port: int = PORT, *, client_id: str, client_secret: str,
                         store: TokenStore, use_tls: bool = True) -> Tokens:
    """Swap the stored refresh token for a new access + refresh token pair and save both.
    Needs only application auth, so it works after the access token has expired."""
    current = store.load()
    client = CTraderClient(host, port, client_id=client_id, client_secret=client_secret,
                           access_token=current.access_token, account_id=0, use_tls=use_tls)
    await client.connect()
    try:
        await client._request(M.ProtoOAApplicationAuthReq(
            clientId=client_id, clientSecret=client_secret))
        res = await client._request(M.ProtoOARefreshTokenReq(refreshToken=current.refresh_token))
    finally:
        await client.close()
    tokens = Tokens(access_token=res.accessToken, refresh_token=res.refreshToken)
    store.save(tokens)
    return tokens
