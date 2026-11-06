import asyncio
import time
from typing import Any

from salmon_claude.core.context import ExecutionContext
from salmon_claude.core.events.bus import EventBus
from salmon_claude.llm.base import LLMProvider
from salmon_claude.permissions.manager import PermissionManager
from salmon_claude.tools.invocation import invoke_tool
from salmon_claude.tools.registry import ToolRegistry
from salmon_claude.tools.truncate import MAX_TOOL_OUTPUT_CHARS, truncate_for_model


class AgentLoop:
    """把模型和工具连起来的 plan→act→observe 循环。

    输入：provider / registry / permissions（None=只读模式）/ max_tool_output_chars /
        bus（可选，给就发 llm.token、llm.usage、工具事件）
    输出：无（结果写进 context.status/result）
    功能：问模型（流式则逐 token 广播）→ 记录 assistant 消息 → 执行工具（广播起止）→ 回填结果。
    """

    def __init__(self, provider: LLMProvider, registry: ToolRegistry, permissions: PermissionManager | None = None, max_tool_output_chars: int = MAX_TOOL_OUTPUT_CHARS, bus: EventBus | None = None) -> None:
        """注入依赖：模型、工具表、权限管理器、工具输出截断上限、事件总线。"""
        self._provider = provider
        self._registry = registry
        self._permissions = permissions
        self._max_tool_output_chars = max_tool_output_chars
        self._bus = bus

    def _messages_for_model(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """造一份「模型视图」：只截断 tool_result 的 content，不改原始记录。

        输入：messages（工作上下文的完整消息列表，含完整工具输出）
        输出：新列表（tool_result 的 content 被截断，其余不变）
        功能：把超长工具输出压到 max_tool_output_chars 内，只影响这次给模型的输入；
            context.messages 仍保留完整内容，供运行记录落盘
        """
        view: list[dict[str, Any]] = []
        for m in messages:
            content = m.get("content")
            if isinstance(content, list):
                new_blocks: list[dict[str, Any]] = []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        block = dict(block)   # 拷贝，避免改到原记录
                        text = block.get("content")
                        if isinstance(text, str):
                            block["content"] = truncate_for_model(text, self._max_tool_output_chars)
                    new_blocks.append(block)
                content = new_blocks
            view.append({**m, "content": content})
        return view

    async def _publish(self, event_type: str, run_id: str, data: dict[str, Any]) -> None:
        """把一条四字段事件广播到 bus（无 bus 时跳过）。

        输入：event_type（事件名）、run_id、data（负载）
        输出：无
        功能：拼 type/run_id/timestamp/data 四字段，await bus.publish 广播。
        """
        if self._bus is None:
            return
        await self._bus.publish({
            "type": event_type,
            "run_id": run_id,
            "timestamp": time.time(),
            "data": data,
        })

    def _token_callback(self, run_id: str):
        """造一个 on_token 回调：把每个文本 chunk 广播成 llm.token 事件。

        输入：run_id
        输出：async (token: str) -> None
        功能：闭包捕获 run_id，每收到一个 token 就 _publish("llm.token", ...)。
        """
        async def on_token(token: str) -> None:
            await self._publish("llm.token", run_id, {"token": token})
        return on_token

    async def run(self, context: ExecutionContext) -> None:
        """驱动循环直到结束；结果写进 context.status/result。"""
        while not context.is_done():
            context.step += 1

            # [plan] 问模型（带流式回调，逐 token 广播 llm.token）
            try:
                response = await self._provider.chat(
                    self._messages_for_model(context.messages),
                    on_token=self._token_callback(context.run_id),
                )
            except asyncio.CancelledError:
                context.mark_failed("cancelled")
                raise
            except Exception:
                context.mark_failed("llm_error")
                break

            # 模型回了 usage 就广播一次（供 TUI 显示 token 用量）
            if response.usage is not None:
                await self._publish("llm.usage", context.run_id, {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                })

            # [observe] 把模型返回记成 assistant 消息
            blocks: list[dict[str, Any]] = []
            if response.text:
                blocks.append({"type": "text", "text": response.text})
            for tc in response.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
            context.add_assistant_message(blocks)

            # [act] 执行模型要调的工具，结果回填（工具起止事件由 invoke_tool 发）
            if response.stop_reason == "tool_use":
                for tc in response.tool_calls:
                    try:
                        result = await invoke_tool(self._registry, tc, self._permissions, bus=self._bus, run_id=context.run_id)
                    except asyncio.CancelledError:
                        context.mark_failed("cancelled")
                        raise
                    context.add_tool_result(tc.id, result.content, is_error=result.is_error)

            # 终止判断
            if response.stop_reason == "end_turn":
                context.mark_success(response.text or "")
            elif context.step >= context.max_steps:
                context.mark_failed("exceeded_max_steps")
