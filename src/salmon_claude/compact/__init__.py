"""compact：把历史消息压成一段摘要，减少工作上下文长度。"""

from __future__ import annotations

import json
from typing import Any

from salmon_claude.llm.base import LLMProvider


class Compactor:
    """把一批历史消息压成摘要，保留目标/约束/文件状态/TODO。

    谁调用：daemon 处理 session.compact 时
    输入：provider（模型，用来生成摘要）
    输出：无
    功能：summarize 组装摘要 prompt 调模型，返回摘要文本；模型报错就抛异常，
        由上层决定「保持原工作上下文」的兜底
    """

    def __init__(self, provider: LLMProvider) -> None:
        """保存模型 provider。"""
        self._provider = provider

    async def summarize(self, messages: list[dict[str, Any]]) -> str:
        """把 messages 压成一段中文摘要。

        输入：messages（要摘要的历史消息，role/content 形式）
        输出：str（摘要文本）
        功能：拼「必须保留四要素」的 prompt，丢给模型取文本返回
        """
        prompt = (
            "把下面的对话历史总结成一段中文摘要，必须保留："
            "① 用户目标 ② 约束/偏好 ③ 文件状态（写了/改了哪些文件）④ 未完成的 TODO。\n\n"
            + json.dumps(messages, ensure_ascii=False)
        )
        response = await self._provider.chat([{"role": "user", "content": prompt}])
        return response.text or ""
