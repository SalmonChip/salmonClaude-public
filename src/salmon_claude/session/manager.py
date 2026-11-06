"""SessionManager：内存里管 session 的创建、查找、串行追加。"""

from __future__ import annotations

import asyncio
import uuid

from salmon_claude.session.models import Message, Session
from salmon_claude.session.store import SessionStore
from typing import Any


class SessionManager:
    """会话内存层 + 并发控制。

    谁调用：daemon 处理 start_run 时
    输入：store（落盘层）
    输出：无
    功能：get_or_create 建/取 session；append 在每 session 一把锁下串行追加并落盘；
        同 session 串行、跨 session 并发（各自锁互不干扰）
    """

    def __init__(self, store: SessionStore) -> None:
        """保存落盘层，初始化内存缓存和锁表。"""
        self._store = store
        self._sessions: dict[str, Session] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        """取某 session 的锁，没有就建一把（同 session 串行的关键）。"""
        return self._locks.setdefault(session_id, asyncio.Lock())

    def get_or_create(self, session_id: str | None = None) -> Session:
        """建或取一个 session。

        输入：session_id（None 则新建一个 uuid）
        输出：Session（新建的、缓存的、或从磁盘恢复的）
        功能：None→新建；在内存→直接返回；不在内存但磁盘有→load 恢复并缓存；
            都没有→新建并缓存
        """
        if session_id is None:
            session_id = uuid.uuid4().hex
        if session_id in self._sessions:
            return self._sessions[session_id]
        restored = self._store.load(session_id)
        if restored is not None:
            self._sessions[session_id] = restored
            return restored
        session = Session(session_id=session_id)
        self._sessions[session_id] = session
        return session
    def list_sessions(self) -> list[str]:
        """列出磁盘上所有 session_id（显式恢复入口的「列」）。

        输入：无
        输出：list[str]（所有有 messages.jsonl 的 session_id）
        功能：委托 store.list_sessions()，供 daemon 的 session.list 接口用
        """
        return self._store.list_sessions()

    def list_sessions_info(self) -> list[dict[str, Any]]:
        """列出所有 session 的列表展示信息（供 session.list 接口 / TUI 表格用）。

        谁调用：daemon 处理 session.list 时
        输入：无
        输出：list[dict]（每个含 session_id / name / message_count / updated_at）
        功能：对每个 id 调 store.session_info 聚合展示字段
        """
        return [self._store.session_info(sid) for sid in self._store.list_sessions()]

    async def append(self, session_id: str, message: Message) -> None:
        """串行追加一条消息到 session：加锁 → 更新内存 → 落盘。

        输入：session_id、message（带 message_id）
        输出：无
        功能：用该 session 的锁保证同 session 串行（跨 session 各自锁并发）；
            先更新内存 messages，再同步写盘
        """
        async with self._lock_for(session_id):
            session = self._sessions.get(session_id)
            if session is None:
                session = self.get_or_create(session_id)
            session.messages.append(message)
            self._store.append(session_id, message)
    async def append_message(self, session_id: str, role: str, content: list[dict[str, Any]], run_id: str) -> Message:
        """串行追加一条消息并自动分配 message_id（daemon 落 run 消息用）。

        输入：session_id、role、content、run_id（message_id 不传，由本方法分配）
        输出：新加的 Message（含分配好的 message_id）
        功能：在该 session 锁下，用「原始日志当前长度 + 1」生成 m0001 形式 id，
            包成 Message 加进内存并落盘；同 session 串行时 id 天然连续
        """
        async with self._lock_for(session_id):
            session = self._sessions.get(session_id)
            if session is None:
                session = self.get_or_create(session_id)
            message_id = f"m{len(session.messages) + 1:04d}"
            message = Message(message_id=message_id, role=role, content=content, run_id=run_id)
            session.messages.append(message)
            self._store.append(session_id, message)
            return message
    async def add_note(self, session_id: str, note: str) -> list[str]:
        """往 session 追加一条笔记（持久约束），返回完整笔记列表。

        输入：session_id、note（一条笔记文本）
        输出：list[str]（加完后的完整笔记）
        功能：在该 session 锁下取/建 session，追加笔记到内存，整份写回 notes.json；
            run 开始时才读进工作上下文（快照语义）
        """
        async with self._lock_for(session_id):
            session = self._sessions.get(session_id)
            if session is None:
                session = self.get_or_create(session_id)
            session.notes.append(note)
            self._store.save_notes(session_id, session.notes)
            return list(session.notes)

    async def compact(self, session_id: str, summary: str, until_message_id: str) -> None:
        """记录一次压缩结果：摘要文本 + 摘要到的 message_id。

        输入：session_id、summary（摘要文本）、until_message_id（最后一个被摘要的消息 id）
        输出：无
        功能：在该 session 锁下更新内存 summary/compacted_until 并落盘；
            工作上下文后续只取 message_id > until_message_id 的新消息
        """
        async with self._lock_for(session_id):
            session = self._sessions.get(session_id)
            if session is None:
                session = self.get_or_create(session_id)
            session.summary = summary
            session.compacted_until = until_message_id
            self._store.save_summary(session_id, summary, until_message_id)
