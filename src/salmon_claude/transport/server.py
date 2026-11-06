"""传输层服务端：常驻守护进程的 socket 入口。

只绑本机 127.0.0.1；每条连接开一个读循环，逐行读 NDJSON 请求，交给 on_request 处理。
内置默认处理只有 ping；daemon 传入 on_request 后可处理 start_run / subscribe / respond 等。
每条连接有订阅表（默认空），broadcast 只把事件推给订阅了该 run（或订阅全部）的连接。
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from salmon_claude.transport.protocol import (
    METHOD_PING,
    ProtocolError,
    encode_message,
    make_response,
    parse_message,
)

# 服务端报告给 ping 的版本号（后续可与 cli 的 --version 对齐）
VERSION = "0.1.0"

# 请求处理器类型：async (message, conn_id) -> response
RequestHandler = Callable[[dict[str, Any], int], Awaitable[dict[str, Any]]]

# 断连回调类型：async (conn_id) -> None
DisconnectHandler = Callable[[int], Awaitable[None]]


class Server:
    """TCP 服务端：接收客户端连接，读请求、回响应，并按订阅广播事件。
    

    谁调用：salmon-core（daemon 传 on_request）或单测（不传，用内置 ping）
    输入：host（默认 127.0.0.1）、port（默认 0=系统随机）、on_request（可选请求处理器）
    输出：无（start() 后常驻等连接）
    功能：起服务；每条连接一个读循环；请求交给 on_request（缺省只处理 ping）；
        每条连接有订阅表（默认空），broadcast 按 run_id 过滤推送事件
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        on_request: RequestHandler | None = None,
        on_disconnect: DisconnectHandler | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._on_request = on_request
        self._on_disconnect = on_disconnect
        self._server: asyncio.Server | None = None
        self._clients: dict[int, asyncio.StreamWriter] = {}      # 连接 id → writer
        self._subscriptions: dict[int, set[str] | None] = {}     # 连接 id → 订阅集合（None=全部）
        self._next_conn_id: int = 0

    @property
    def port(self) -> int:
        """实际监听的端口（port=0 时由系统分配，start 之后才确定）。"""
        return self._port

    async def start(self) -> None:
        """起服务并记录系统实际分配的端口。"""
        self._server = await asyncio.start_server(self._handle_connection, self._host, self._port)
        self._port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """关闭服务，释放端口。"""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    def subscribe(self, conn_id: int, run_id: str | None = None) -> None:
        """把某个连接订阅到某个 run；run_id 为 None 表示订阅全部。

        输入：conn_id（连接 id）、run_id（None=全部，否则具体 run）
        输出：无
        功能：None 则升级为「全部」；否则把 run_id 加进该连接的订阅集合
        """
        subs = self._subscriptions.get(conn_id)
        if subs is None:   # 已经是「全部」，不用细化
            return
        if run_id is None:
            self._subscriptions[conn_id] = None
        else:
            subs.add(run_id)

    async def broadcast(self, message: dict[str, Any], run_id: str | None = None) -> None:
        """把一条消息（通常是事件）推给订阅了该 run 的客户端。

        输入：message（消息 dict）、run_id（事件属于哪个 run）
        输出：无
        功能：遍历连接，订阅为「全部」或 run_id 在订阅集合里才发；单个客户端写失败跳过
        """
        for conn_id, writer in list(self._clients.items()):
            subs = self._subscriptions.get(conn_id)
            if subs is not None and run_id is not None and run_id not in subs:
                continue   # 这条连接没订阅这个 run，跳过
            try:
                writer.write(encode_message(message))
                await writer.drain()
            except (ConnectionError, OSError):
                pass   # 对端可能已断开，交给读循环的 EOF 分支清理

    async def _handle_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """一条连接的读循环：分配 id、读行 → 解析 → 分发 → 写回，断开或坏帧时退出。"""
        conn_id = self._next_conn_id
        self._next_conn_id += 1
        self._clients[conn_id] = writer
        self._subscriptions[conn_id] = set()   # 默认订阅空
        try:
            while True:
                line = await reader.readline()
                if not line:   # 对端断开（EOF）
                    break
                message = parse_message(line)   # 坏帧/超长帧抛 ProtocolError
                if message.get("type") != "request":
                    continue
                response = await self._dispatch(message, conn_id)
                writer.write(encode_message(response))
                await writer.drain()
        except ProtocolError:
            pass   # 帧非法，直接断开
        finally:
            if self._on_disconnect is not None:
                await self._on_disconnect(conn_id)
            self._clients.pop(conn_id, None)
            self._subscriptions.pop(conn_id, None)
            writer.close()
            await writer.wait_closed()


    async def _dispatch(self, message: dict[str, Any], conn_id: int) -> dict[str, Any]:
        """把一条请求分发给外部处理器（有 on_request 时）或内置 ping 处理。

        输入：message（一条 request 消息）、conn_id（发这条请求的连接 id）
        输出：一条 response 消息
        功能：on_request 存在则全权交给它；否则只处理 ping，其余回 error
        """
        if self._on_request is not None:
            return await self._on_request(message, conn_id)
        request_id = message["id"]
        method = message.get("method")
        if method == METHOD_PING:
            return make_response(request_id, result={"version": VERSION})
        return make_response(request_id, error=f"unknown method: {method}")
