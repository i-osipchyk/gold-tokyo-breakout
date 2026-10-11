"""An in-process cTrader Open API server for client tests: real framing (4-byte big-endian
length + ProtoMessage, as the library's Int32StringReceiver), scripted replies by request type."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Callable

from ctrader_open_api.messages import OpenApiCommonMessages_pb2 as C
from ctrader_open_api.messages import OpenApiMessages_pb2 as M

Reply = Callable[[object], list]        # request message -> reply messages (may be empty)


def _by_type() -> dict[int, type]:
    out = {}
    for mod in (C, M):
        for name in dir(mod):
            cls = getattr(mod, name)
            if name.startswith("Proto") and hasattr(cls, "DESCRIPTOR") and \
                    "payloadType" in cls.DESCRIPTOR.fields_by_name:
                out[cls().payloadType] = cls
    return out


TYPES = _by_type()


class FakeCTrader:
    def __init__(self) -> None:
        self.received: list = []              # every request message, in order
        self.replies: dict[type, Reply] = {
            M.ProtoOAApplicationAuthReq: lambda r: [M.ProtoOAApplicationAuthRes()],
            M.ProtoOAAccountAuthReq: lambda r: [
                M.ProtoOAAccountAuthRes(ctidTraderAccountId=r.ctidTraderAccountId)],
        }
        self.before_reply: list = []          # unsolicited messages pushed before each reply
        self.errors: list[BaseException] = []  # the fake's own failures, re-raised by tests
        self._server: asyncio.Server | None = None

    def on(self, request_type: type, reply: Reply) -> None:
        self.replies[request_type] = reply

    def of_type(self, request_type: type) -> list:
        return [r for r in self.received if isinstance(r, request_type)]

    async def start(self) -> int:
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while True:
                (size,) = struct.unpack(">I", await reader.readexactly(4))
                envelope = C.ProtoMessage()
                envelope.ParseFromString(await reader.readexactly(size))
                request = TYPES[envelope.payloadType]()
                request.ParseFromString(envelope.payload)
                self.received.append(request)
                reply = self.replies.get(type(request))
                if reply is None:
                    continue
                for msg in self.before_reply:
                    self._send(writer, msg, None)
                for msg in reply(request):
                    self._send(writer, msg, envelope.clientMsgId or None)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        except Exception as e:                # a broken scripted reply: surface it
            self.errors.append(e)
        finally:
            writer.close()

    @staticmethod
    def _send(writer: asyncio.StreamWriter, msg, client_msg_id: str | None) -> None:
        envelope = C.ProtoMessage(payloadType=msg.payloadType, payload=msg.SerializeToString())
        if client_msg_id:
            envelope.clientMsgId = client_msg_id
        data = envelope.SerializeToString()
        writer.write(struct.pack(">I", len(data)) + data)
