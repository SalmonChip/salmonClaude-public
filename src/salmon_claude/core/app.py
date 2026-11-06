"""守护进程应用：把 runner/registry 装进 socket server，接上「启动 run / 事件转发 / 审批 respond」三个接缝。

读循环只负责收发请求；真正的 run 在后台 task 里跑，绝不阻塞读循环。
daemon 为每个 run 自建 bus + PermissionManager 并传给 runner，这样：
- 能订阅 bus 把事件广播给客户端；
- 能按 run_id 找到 PermissionManager 响应审批。
"""

import asyncio
import uuid
from typing import Any

from salmon_claude.compact import Compactor
from salmon_claude.core.events.bus import EventBus
from salmon_claude.core.runner import AgentRunner
from salmon_claude.permissions.manager import PermissionManager
from salmon_claude.permissions.policy import Policy
from salmon_claude.session.manager import SessionManager
from salmon_claude.session.models import message_seq
from salmon_claude.session.store import SessionStore
from salmon_claude.transport.protocol import (
    METHOD_PING,
    METHOD_RESPOND,
    METHOD_SESSION_ADD_NOTE,
    METHOD_SESSION_COMPACT,
    METHOD_SESSION_GET,
    METHOD_SESSION_LIST,
    METHOD_START_RUN,
    METHOD_SUBSCRIBE,
    make_event,
    make_response,
)
from salmon_claude.transport.server import VERSION, Server
from salmon_claude.tools.truncate import MAX_TOOL_OUTPUT_CHARS


class DaemonApp:
    """守护进程应用：装配 runner + server，管理 run 生命周期和审批路由。

    谁调用：salmon-core 入口
    输入：provider（模型）、registry（工具）、workspace_root（工作目录）、host、port
    输出：无（start() 后常驻等连接）
    功能：start 起 server（把请求交给 _handle_request）；_spawn_run 把 run 丢后台 task；
        事件经 bus → broadcast 推到客户端；respond 按 run_id 路由到对应 PermissionManager。
    """

    def __init__(self, provider, registry, workspace_root, sessions: SessionManager | None = None, host: str = "127.0.0.1", port: int = 0, max_tool_output_chars: int = MAX_TOOL_OUTPUT_CHARS) -> None:
        self._runner = AgentRunner(provider, registry, workspace_root, max_tool_output_chars=max_tool_output_chars)  # 不带 approve，审批走 socket
        self._compactor = Compactor(provider)
        self._sessions = sessions if sessions is not None else SessionManager(SessionStore(workspace_root / "sessions"))
        self._host = host
        self._port = port
        self._server: Server | None = None
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._permissions: dict[str, PermissionManager] = {}
        self._run_owner: dict[str, int] = {}   # run_id → 发起该 run 的连接 id
        self._run_session: dict[str, str] = {}   # run_id → session_id
        self._session_busy: set[str] = set()   # 正在跑的 session_id，用于同 session 整轮互斥


    @property
    def port(self) -> int:
        """server 实际监听的端口（start 之后才可用）。"""
        if self._server is None:
            raise RuntimeError("server 还没 start")
        return self._server.port

    async def start(self) -> None:
        """起服务，把所有请求交给 self._handle_request。"""
        self._server = Server(
            self._host,
            self._port,
            on_request=self._handle_request,
            on_disconnect=self._on_disconnect,
        )

        await self._server.start()

    async def stop(self) -> None:
        """取消所有后台 run、等它们收尾，再关 server 释放端口。"""
        tasks = list(self._runs.values())
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self._server is not None:
            await self._server.stop()

    async def _handle_request(self, message: dict[str, Any], conn_id: int) -> dict[str, Any]:
        """按 method 分发一条请求。

        输入：message（一条 request 消息）、conn_id（发这条请求的连接 id）
        输出：一条 response 消息
        功能：ping 回 version；subscribe 登记订阅；start_run 建后台 run；respond 唤醒审批；未知回 error
        """
        request_id = message["id"]
        method = message.get("method")
        params = message.get("params", {})
        if method == METHOD_PING:
            return make_response(request_id, result={"version": VERSION})
        if method == METHOD_SUBSCRIBE:
            self._server.subscribe(conn_id, params.get("run_id"))
            return make_response(request_id, result={"ok": True})
        if method == METHOD_START_RUN:
            return self._handle_start_run(request_id, params, conn_id)
        if method == METHOD_RESPOND:
            return self._handle_respond(request_id, params)
        if method == METHOD_SESSION_LIST:
            return self._handle_session_list(request_id, params)
        if method == METHOD_SESSION_GET:
            return self._handle_session_get(request_id, params)
        if method == METHOD_SESSION_ADD_NOTE:
            return await self._handle_add_note(request_id, params)
        if method == METHOD_SESSION_COMPACT:
            return await self._handle_compact(request_id, params)
        return make_response(request_id, error=f"unknown method: {method}")


    def _handle_start_run(self, request_id: str, params: dict[str, Any], conn_id: int) -> dict[str, Any]:
        """处理 start_run：同 session 互斥、取笔记+摘要+新消息、后台跑、记录归属，立刻回 run_id + session_id。

        功能：同一 session 上轮还没跑完就再来一个 start_run，直接回 busy 错误（整轮串行），
            避免两个 run 都基于同一份旧历史做决策；跨 session 不受影响。
        """
        goal = params.get("goal", "")
        max_steps = params.get("max_steps", 10)
        session = self._sessions.get_or_create(params.get("session_id"))
        if session.session_id in self._session_busy:   # 同 session 整轮互斥
            return make_response(request_id, error=f"session busy: {session.session_id}")
        history: list[dict[str, Any]] = []
        if session.notes:   # 笔记放最前，作为每轮的持久约束（快照：此刻定死）
            history.append({"role": "user", "content": [{"type": "text", "text": "[项目笔记]\n" + "\n".join(session.notes)}]})
        if session.summary:   # 摘要替代被压掉的旧消息
            history.append({"role": "user", "content": [{"type": "text", "text": "[历史摘要]\n" + session.summary}]})
        for m in session.messages:   # 只取摘要点之后的新消息（按整数序号比，避免 m10000<m9999 的字符串坑）
            if session.compacted_until is None or message_seq(m.message_id) > message_seq(session.compacted_until):
                history.append({"role": m.role, "content": m.content})
        self._session_busy.add(session.session_id)
        run_id = self._spawn_run(goal, max_steps, session.session_id, history)
        self._run_owner[run_id] = conn_id
        return make_response(request_id, result={"run_id": run_id, "session_id": session.session_id})

    def _handle_session_list(self, request_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """处理 session.list：列出所有 session 的展示信息（id/名称/消息数/更新时间）。"""
        return make_response(request_id, result={"sessions": self._sessions.list_sessions_info()})

    def _handle_session_get(self, request_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """处理 session.get：取某 session 的全部消息（顺带做显式恢复）。"""
        session_id = params.get("session_id")
        if session_id is None:
            return make_response(request_id, error="session.get 需要 session_id")
        session = self._sessions.get_or_create(session_id)
        return make_response(request_id, result={
            "session_id": session.session_id,
            "messages": [m.to_dict() for m in session.messages],
        })
    async def _handle_add_note(self, request_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """处理 session.add_note：给某 session 追加一条持久笔记。"""
        session_id = params.get("session_id")
        note = params.get("note")
        if session_id is None or not note:
            return make_response(request_id, error="session.add_note 需要 session_id 和 note")
        notes = await self._sessions.add_note(session_id, note)
        return make_response(request_id, result={"notes": notes})

    async def _handle_compact(self, request_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """处理 session.compact：把该 session 的历史压成摘要，失败保持原状。

        功能：消息快照和截止 ID 都在 await 模型**之前**定死——await 期间新增的消息
            序号比截止点大，压缩后仍会进入下一轮上下文；空摘要不推进截止点。
        """
        session_id = params.get("session_id")
        if session_id is None:
            return make_response(request_id, error="session.compact 需要 session_id")
        session = self._sessions.get_or_create(session_id)
        if not session.messages:
            return make_response(request_id, error="session 没有消息可压缩")
        msgs = [{"role": m.role, "content": m.content} for m in session.messages]
        until = session.messages[-1].message_id   # 截止点先固定，await 期间新消息不受影响
        try:
            summary = await self._compactor.summarize(msgs)
        except Exception as exc:
            return make_response(request_id, error=f"compact 失败: {exc}")
        if not summary:   # 空摘要不推进截止点，保持原状态
            return make_response(request_id, error="compact 失败: 空摘要")
        await self._sessions.compact(session_id, summary, until)
        return make_response(request_id, result={"summary": summary, "compacted_until": until})


    def _spawn_run(self, goal: str, max_steps: int, session_id: str, history: list[dict[str, Any]]) -> str:
        """建一个 run 的后台 task：自建 bus + permissions，记录 session 归属，create_task 跑起来。"""
        run_id = uuid.uuid4().hex
        bus = EventBus()
        permissions = PermissionManager(policy=Policy(), bus=bus, run_id=run_id)
        self._permissions[run_id] = permissions
        self._run_session[run_id] = session_id

        async def forward(event: dict[str, Any]) -> None:
            await self._server.broadcast(make_event(event["type"], event["run_id"], event["data"]), event["run_id"])

        bus.subscribe(forward)
        task = asyncio.create_task(self._run_guarded(goal, max_steps, run_id, bus, permissions, session_id, history))
        self._runs[run_id] = task
        return run_id


    async def _run_guarded(
        self,
        goal: str,
        max_steps: int,
        run_id: str,
        bus: EventBus,
        permissions: PermissionManager,
        session_id: str,
        history: list[dict[str, Any]],
    ) -> None:
        """跑 run 并兜底：结束后把消息落进 session 原始日志，异常不炸、收尾摘表。"""
        try:
            context = await self._runner.run_and_capture(goal, max_steps, run_id=run_id, bus=bus, permissions=permissions, history=history)
            # context.messages = history + 本轮 goal + 本轮新增消息；只落本轮新增（跳过 history），
            # 否则多轮时每轮都会把历史（含笔记/摘要）再写一遍，造成 session 日志重复
            for m in context.messages[len(history):]:
                content = m["content"]
                if isinstance(content, str):   # goal 那条是裸字符串，统一包成 text block
                    content = [{"type": "text", "text": content}]
                await self._sessions.append_message(session_id, m["role"], content, run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[daemon] run {run_id} 失败: {exc}")
        finally:
            permissions.cancel_all()
            self._run_owner.pop(run_id, None)
            self._run_session.pop(run_id, None)
            self._runs.pop(run_id, None)
            self._permissions.pop(run_id, None)
            self._session_busy.discard(session_id)


    def _handle_respond(self, request_id: str, params: dict[str, Any]) -> dict[str, Any]:
        """处理 respond：按 run_id 找到 PermissionManager，唤醒挂起的审批。"""
        run_id = params.get("run_id")
        allow = params.get("allow", False)
        permissions = self._permissions.get(run_id)
        if permissions is None:
            return make_response(request_id, error=f"unknown run: {run_id}")
        ok = permissions.respond(params.get("request_id"), allow)
        return make_response(request_id, result={"ok": ok})
    async def _on_disconnect(self, conn_id: int) -> None:
        """连接断开时，取消该连接名下所有 run 的挂起审批。

        谁调用：Server 的 on_disconnect 回调（读循环 finally 里）
        输入：conn_id（刚断开的连接 id）
        输出：无
        功能：遍历 _run_owner 找出属于该 conn 的 run，对其 PermissionManager 调 cancel_all()
            取消 pending Future → run 的 check_and_wait 抛 CancelledError → run 以 cancelled 结束
        """
        for run_id, owner in list(self._run_owner.items()):
            if owner != conn_id:
                continue
            permissions = self._permissions.get(run_id)
            if permissions is not None:
                permissions.cancel_all()
