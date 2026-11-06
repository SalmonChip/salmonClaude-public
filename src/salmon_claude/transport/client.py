"""传输层客户端：连守护进程、发请求、收响应和事件。

只允许一个读循环；请求按 request_id 挂 Future，读循环收到 response 后按 id 唤醒。
本步只有 request()（事件处理留空，后续接 TUI）。
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from salmon_claude.transport.protocol import (
    ProtocolError,
    encode_message,
    make_request,
    parse_message,
)

# 事件回调签名：async (event: dict) -> None
EventHandler = Callable[[dict[str, Any]], Awaitable[None]]


class Client:
    """TCP 客户端：连服务端，发请求挂 Future，单读循环按 id 回填。

    谁调用：salmon ping、后续 salmon-tui
    输入：reader/writer（由 connect 建立）、timeout（单请求超时秒数）
    输出：无（request() 返回服务端的 response）
    功能：open_connection 连上后启动唯一读循环；request() 用 Future 挂起等回执
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, timeout: float = 30.0) -> None:
        self._reader = reader
        self._writer = writer
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._on_event: EventHandler | None = None
        self._read_task: asyncio.Task[None] | None = None

    @classmethod
    async def connect(cls, host: str, port: int, timeout: float = 30.0) -> "Client":
        """连上服务端并启动读循环，返回就绪的 Client。"""
        reader, writer = await asyncio.open_connection(host, port)
        client = cls(reader, writer, timeout)
        client._read_task = asyncio.create_task(client._reader_loop())
        return client

    def set_event_handler(self, handler: EventHandler) -> None:
        """注册事件回调（后续 TUI 用它更新画面）。"""
        self._on_event = handler

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发一个请求并等待回执。

        输入：method（操作名）、params（参数）
        输出：服务端回的那条 response 消息（含 ok / result 或 error）
        功能：生成 request_id → 登记 Future → 写 NDJSON → await 回执（带超时）
        """
        request_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut
        try:
            self._writer.write(encode_message(make_request(request_id, method, params)))
            await self._writer.drain()
            return await asyncio.wait_for(fut, timeout=self._timeout)
        finally:
            self._pending.pop(request_id, None)

            
    async def subscribe(self, run_id: str | None = None) -> dict[str, Any]:
        """订阅事件：None=订阅全部，具体 run_id=只订阅该 run。

        输入：run_id（可选，None 订阅全部）
        输出：服务端的 ack response
        功能：等价于 request("subscribe", {"run_id": run_id})
        """
        return await self.request("subscribe", {"run_id": run_id})


    async def _reader_loop(self) -> None:
        """唯一读循环：逐行读 NDJSON，response 按 id 唤醒，event 交给回调。"""
        try:
            while True:
                line = await self._reader.readline()
                if not line:   # 服务端断开
                    break
                message = parse_message(line)
                msg_type = message.get("type")
                if msg_type == "response":
                    self._complete_pending(message)
                elif msg_type == "event":
                    await self._dispatch_event(message)
        except ProtocolError:
            pass   # 帧非法，结束读循环
        finally:
            self._cancel_all_pending()

    def _complete_pending(self, response: dict[str, Any]) -> None:
        """按 response 的 id 找到对应 Future 并填结果；没找到（已超时/取消）则忽略。"""
        fut = self._pending.pop(response.get("id"), None)
        if fut is not None and not fut.done():
            fut.set_result(response)

    async def _dispatch_event(self, event: dict[str, Any]) -> None:
        """把事件交给外部回调（本步默认无回调，空过）。"""
        if self._on_event is not None:
            await self._on_event(event)

    def _cancel_all_pending(self) -> None:
        """连接断开时取消所有还没等到回执的 Future，不让它们永远挂着。"""
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()

    async def close(self) -> None:
        """关闭连接：停读循环 → 关 writer。"""
        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except asyncio.CancelledError:
                pass
        self._writer.close()
        await self._writer.wait_closed()
