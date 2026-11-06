"""MCP stdio 客户端：spawn server 子进程，用 JSON-RPC 2.0（新行分隔 JSON）对话。"""

import asyncio
import json
from asyncio.subprocess import Process
from typing import Any


class McpClient:
    """MCP stdio 客户端。

    谁调用：adapter（把 MCP 工具接进 registry）、测试
    输入：command（启动 server 的命令列表，如 ["python", "-m", "salmon_claude.mcp.fake_server"]）
    输出：无（方法返回响应）
    功能：start 起子进程拿 stdin/stdout；request 发带自增 id 的 JSON-RPC 请求、
        循环读行直到同 id 的响应；initialize / list_tools / call_tool 是便捷封装；close 终止
    """

    def __init__(self, command: list[str]) -> None:
        """保存启动命令，id 计数器归零，进程句柄置 None。"""
        self._command = command
        self._next_id = 0
        self._process: Process | None = None

    async def start(self) -> None:
        """起 server 子进程，拿 stdin（写）/ stdout（读）。"""
        self._process = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
        )

    def _decode_line(self, raw: bytes) -> str:
        """把子进程输出的一行 bytes 解码成 str：先 UTF-8，失败回退 GBK。

        谁调用：request()（读 MCP server 的 stdout 行）
        输入：raw（一行原始字节）
        输出：str（解码后的文本）
        功能：Windows 上子进程默认用系统编码（GBK）打印，直接 utf-8 会抛
            UnicodeDecodeError；这里 UTF-8 失败就回退 GBK，兼容两类编码的 server。
        """
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw.decode("gbk", errors="replace")

    async def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """发一个 JSON-RPC 请求并等它的响应。

        输入：method（如 "initialize"）、params（参数字典）
        输出：响应的 result 字段；server 回 error 则抛 RuntimeError
        功能：id 自增 → 写一行请求并 flush → 循环读 stdout 每行 JSON，
            跳过无 id 的通知，读到同 id：有 error 抛、否则返回 result
        """
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("client 还没 start")
        self._next_id += 1
        req_id = self._next_id
        line = json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        self._process.stdin.write((line + "\n").encode("utf-8"))
        await self._process.stdin.drain()

        while True:
            raw = await self._process.stdout.readline()
            if not raw:   # EOF：server 关了
                raise RuntimeError(f"MCP server 关闭，未收到 id={req_id} 的响应")
            msg = json.loads(self._decode_line(raw))
            if msg.get("id") != req_id:
                continue   # 跳过通知 / 别的 id 的响应
            if "error" in msg:
                raise RuntimeError(f"MCP error: {msg['error']}")
            return msg.get("result", {})

    async def initialize(self) -> dict[str, Any]:
        """发 initialize（空 params），返回 server 的协议信息。"""
        return await self.request("initialize", {})

    async def list_tools(self) -> list[dict[str, Any]]:
        """发 tools/list，返回 result["tools"]。"""
        result = await self.request("tools/list", {})
        return result.get("tools", [])

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """发 tools/call，返回该工具一次执行的结果。"""
        return await self.request("tools/call", {"name": name, "arguments": arguments})

    async def close(self) -> None:
        """终止子进程、等它退出。"""
        if self._process is not None and self._process.returncode is None:
            self._process.terminate()
            await self._process.wait()
