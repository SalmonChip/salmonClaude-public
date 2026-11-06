from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from salmon_claude.llm.types import LlmResponse

# 流式回调：async (token: str) -> None，模型每吐一个文本 chunk 就调一次
TokenCallback = Callable[[str], Awaitable[None]]


class LLMProvider(Protocol):
    """任何模型提供者（假模型 / 真模型）都必须满足的接口。"""

    async def chat(
        self,
        messages: list[dict[str, Any]],
        on_token: TokenCallback | None = None,
    ) -> LlmResponse:
        """给模型发一轮消息，返回一次完整响应。

        输入：messages（消息列表）、on_token（可选，流式时逐 chunk 回调）
        输出：LlmResponse
        功能：on_token 为 None 就一次性返回；给 on_token 则边生成边回调文本 chunk。
        """
        ...
