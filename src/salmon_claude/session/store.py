"""SessionStore：把 session 的原始日志落盘、读回、列出来。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from salmon_claude.session.models import Message, Session

_NAME_MAX = 20   # 会话显示名最大字数，超长截断加省略号


class SessionStore:
    """会话持久化层：负责文件系统上的读写。

    谁调用：SessionManager
    输入：root（sessions 根目录）
    输出：无
    功能：append 追加写 JSONL；load 重建 Session；list_sessions 列 session id。
        存储布局：root/{session_id}/messages.jsonl，每行一条 Message 的 JSON。
    """

    def __init__(self, root: Path) -> None:
        """保存 sessions 根目录。"""
        self._root = root

    def _session_dir(self, session_id: str) -> Path:
        """某 session 的目录路径。"""
        return self._root / session_id

    def _log_path(self, session_id: str) -> Path:
        """某 session 的日志文件路径。"""
        return self._session_dir(session_id) / "messages.jsonl"
    def _notes_path(self, session_id: str) -> Path:
        """某 session 的笔记文件路径（与消息日志分开存）。"""
        return self._session_dir(session_id) / "notes.json"

    def _summary_path(self, session_id: str) -> Path:
        """某 session 的摘要文件路径（与消息日志分开存）。"""
        return self._session_dir(session_id) / "summary.json"

    def append(self, session_id: str, message: Message) -> None:
        """把一条消息追加写到该 session 的日志末尾（只增不改）。

        输入：session_id、message（带 message_id）
        输出：无
        功能：确保目录存在后，以 JSON 一行追加
        """
        self._session_dir(session_id).mkdir(parents=True, exist_ok=True)
        with self._log_path(session_id).open("a", encoding="utf-8") as f:
            f.write(json.dumps(message.to_dict(), ensure_ascii=False) + "\n")

    def load(self, session_id: str) -> Session | None:
        """从磁盘重建一个 Session；日志文件不存在返回 None。

        输入：session_id
        输出：Session（按 message_id 顺序读回）；不存在则 None
        功能：逐行读 JSONL 还原 Message；坏行（JSON 解析失败）跳过并继续，
            保证坏尾不炸掉整段日志（明确恢复策略）
        """
        path = self._log_path(session_id)
        if not path.exists():
            return None
        messages: list[Message] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                messages.append(Message.from_dict(json.loads(line)))
            except json.JSONDecodeError:
                continue   # 坏行跳过，不因一行损坏丢掉其余日志
        summary, compacted_until = self._load_summary(session_id)
        return Session(
            session_id=session_id,
            messages=messages,
            notes=self._load_notes(session_id),
            summary=summary,
            compacted_until=compacted_until,
        )
    def _load_notes(self, session_id: str) -> list[str]:
        """读该 session 的笔记；文件不存在或损坏当作空列表。"""
        path = self._notes_path(session_id)
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except json.JSONDecodeError:
            return []   # 笔记文件坏掉只影响笔记，不影响消息日志

    def save_notes(self, session_id: str, notes: list[str]) -> None:
        """把该 session 的笔记整份写回 notes.json（覆盖写）。

        输入：session_id、notes（当前完整笔记列表）
        输出：无
        功能：确保目录存在后整份覆盖写；笔记是状态、不是追加日志
        """
        self._session_dir(session_id).mkdir(parents=True, exist_ok=True)
        self._notes_path(session_id).write_text(
            json.dumps(notes, ensure_ascii=False), encoding="utf-8"
        )

    def _load_summary(self, session_id: str) -> tuple[str | None, str | None]:
        """读该 session 的摘要状态；文件不存在或损坏当作未压缩。"""
        path = self._summary_path(session_id)
        if not path.exists():
            return None, None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return None, None
            return data.get("summary"), data.get("compacted_until")
        except json.JSONDecodeError:
            return None, None   # 摘要坏掉当作未压缩，不影响消息日志

    def save_summary(self, session_id: str, summary: str, compacted_until: str) -> None:
        """把该 session 的摘要状态写回 summary.json（覆盖写）。

        输入：session_id、summary（摘要文本）、compacted_until（摘要到的 message_id）
        输出：无
        功能：确保目录存在后覆盖写 summary.json；摘要是有损状态、不是审计记录
        """
        self._session_dir(session_id).mkdir(parents=True, exist_ok=True)
        self._summary_path(session_id).write_text(
            json.dumps({"summary": summary, "compacted_until": compacted_until}, ensure_ascii=False),
            encoding="utf-8",
        )

    def list_sessions(self) -> list[str]:
        """列出所有 session id。

        输入：无
        输出：session_id 列表（按目录名排序）
        功能：遍历 root 下目录，有 messages.jsonl 的才算一个 session
        """
        if not self._root.exists():
            return []
        ids = []
        for d in sorted(self._root.iterdir()):
            if d.is_dir() and (d / "messages.jsonl").exists():
                ids.append(d.name)
        return ids

    def session_name(self, session_id: str) -> str:
        """取某 session 的显示名：首条消息（第一次 run 的 goal）文本，截断到 20 字。

        谁调用：SessionManager.list_sessions_with_names
        输入：session_id
        输出：str（截断后的名称；读不到或首条无文本则回退 session_id 前 12 位）
        功能：读 messages.jsonl 第一行，抽 content 里第一个 text block 的文本。
            新 session 历史为空，goal 最先落盘，所以首条消息就是该 session 的第一句话。
        """
        try:
            lines = self._log_path(session_id).read_text(encoding="utf-8").splitlines()
            if lines:
                first = json.loads(lines[0])
                content = first.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = str(block.get("text", "")).strip()
                            if text:
                                return text[:_NAME_MAX] + ("…" if len(text) > _NAME_MAX else "")
        except (json.JSONDecodeError, OSError):
            pass
        return session_id[:12]

    def session_info(self, session_id: str) -> dict[str, Any]:
        """聚合某 session 的列表展示信息：id + 名称 + 消息数 + 更新时间。

        谁调用：SessionManager.list_sessions_info
        输入：session_id
        输出：dict（session_id / name / message_count / updated_at）
        功能：message_count 数 messages.jsonl 的非空行；updated_at 取文件 mtime；
            name 复用 session_name（首条消息文本，截断 20 字）
        """
        path = self._log_path(session_id)
        message_count = 0
        updated_at = 0.0
        if path.exists():
            updated_at = os.path.getmtime(path)
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    message_count += 1
        return {
            "session_id": session_id,
            "name": self.session_name(session_id),
            "message_count": message_count,
            "updated_at": updated_at,
        }
