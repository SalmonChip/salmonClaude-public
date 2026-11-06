"""session 数据模型：Message / Session 两个 dataclass，是会话持久化的最小单元。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def message_seq(message_id: str) -> int:
    """把 m0001 形式的 message_id 转成整数序号，供跨四位边界的数值比较。

    谁调用：daemon 组装工作上下文时判断「哪些消息在摘要点之后」
    输入：message_id（"m0001" 形式字符串）
    输出：int（"m0001" → 1）
    功能：id 用 %04d 只是最小宽度；序号到 10000 时字符串比较会出错
        （"m10000" < "m9999" 因为 '1' < '9'），所以统一转 int 再比。
    """
    return int(message_id[1:])


@dataclass
class Message:
    """一条带稳定 id 的消息（原始日志的最小单元）。

    输入：message_id（session 内递增，持久化游标靠它）、role（user/assistant）、
        content（顶层 block 列表：text/tool_use/tool_result）、run_id（产生它的 run）
    输出：无（纯数据）
    功能：把「谁、说了什么、哪次 run」捆成一个可落盘的对象
    """

    message_id: str
    role: str
    content: list[dict[str, Any]]
    run_id: str

    def to_dict(self) -> dict[str, Any]:
        """转成可 JSON 序列化的 dict（store 落盘用）。"""
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "run_id": self.run_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        """从 JSON 解析出的 dict 还原 Message（store 读盘用）。"""
        return cls(
            message_id=data["message_id"],
            role=data["role"],
            content=data["content"],
            run_id=data["run_id"],
        )


@dataclass
class Session:
    """一次会话容器：原始消息日志 + 持久笔记 + 压缩摘要。

    输入：session_id、messages（默认空）、notes（默认空）、summary（默认 None）、
        compacted_until（默认 None）、created_at（默认 0）
    输出：无（纯数据）
    功能：持有该 session 的全部原始消息、笔记和压缩状态；summary/compacted_until
        记录「哪些消息已被摘要」，工作上下文据此只取摘要点之后的新消息
    """

    session_id: str
    messages: list[Message] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    summary: str | None = None
    compacted_until: str | None = None
    created_at: float = 0.0

