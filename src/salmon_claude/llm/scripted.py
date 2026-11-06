from typing import Any

from salmon_claude.llm.types import LlmResponse


class ScriptedProvider:

    def __init__(self, replies: list[LlmResponse]) -> None:
        """预设响应列表，并准备一个空列表记录每次收到的请求。"""
        self.replies = list(replies)  # 复制一份，防止外部那个列表被误改
        self.requests: list[list[dict[str, Any]]] = []  # 记录每次 chat 收到的 messages

    async def chat(self, messages: list[dict[str, Any]], on_token=None) -> LlmResponse:
        """给模型发一轮消息，返回一次完整响应。

        输入：messages（消息列表）、on_token（忽略——假模型不流式，只一次返回）
        输出：LlmResponse
        功能：记录请求、弹出一个预设响应；on_token 保持签名兼容但不使用。
        """
        res_messages = list(messages)
        self.requests.append(res_messages)
        if not self.replies:
            raise RuntimeError ("No replies preloaded!")
        return self.replies.pop(0)
        