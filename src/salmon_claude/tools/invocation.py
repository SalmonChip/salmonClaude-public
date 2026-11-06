import time
from typing import Any

from salmon_claude.core.events.bus import EventBus
from salmon_claude.llm.types import ToolCallBlock
from salmon_claude.permissions.manager import PermissionManager
from salmon_claude.tools.base import ToolResult
from salmon_claude.tools.registry import ToolRegistry


async def invoke_tool(
    registry: ToolRegistry,
    tool_call: ToolCallBlock,
    permissions: PermissionManager | None = None,
    bus: EventBus | None = None,
    run_id: str | None = None,
) -> ToolResult:
    """执行一次工具调用；任何失败都转成错误结果，不抛异常。

    输入：registry（工具表）、tool_call（要调的工具）、permissions（权限管理器，None=只读模式）、
        bus / run_id（可选，用于发布 tool.call_started / tool.call_finished 事件）
    输出：ToolResult（内容或错误）
    功能：查工具 → 发 started 事件 → 需要权限的先审批（无管理器或拒绝则拒绝）→ 执行；
        执行抛异常转成 is_error；最后发 finished 事件（含耗时和是否出错）。
    """
    tool = registry.get(tool_call.name)
    if tool is None:
        return ToolResult(f"unknown tool: {tool_call.name}", is_error=True)

    async def emit(event_type: str, data: dict[str, Any]) -> None:
        """发一条工具事件（无 bus/run_id 时跳过）。"""
        if bus is not None and run_id is not None:
            await bus.publish({
                "type": event_type,
                "run_id": run_id,
                "timestamp": time.time(),
                "data": data,
            })

    await emit("tool.call_started", {
        "tool_name": tool_call.name,
        "tool_call_id": tool_call.id,
        "params": tool_call.input,
    })

    # 需要权限的工具先过审批
    if tool.needs_permission:
        if permissions is None:
            # 没装配审批链还调用写工具 → 直接拒（fail-safe，绝不静默放行）
            await emit("tool.call_finished", {
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
                "is_error": True,
                "duration_ms": 0,
            })
            return ToolResult("permission denied: no approval configured", is_error=True)
        allowed = await permissions.check_and_wait(tool_call.name, tool_call.input)
        if not allowed:
            await emit("tool.call_finished", {
                "tool_name": tool_call.name,
                "tool_call_id": tool_call.id,
                "is_error": True,
                "duration_ms": 0,
            })
            return ToolResult("permission denied", is_error=True)

    # 执行，失败转 is_error；耗时用于 finished 事件
    start = time.monotonic()
    try:
        result = await tool.invoke(tool_call.input)
    except Exception as exc:
        result = ToolResult(content=str(exc), is_error=True)
    duration_ms = int((time.monotonic() - start) * 1000)

    await emit("tool.call_finished", {
        "tool_name": tool_call.name,
        "tool_call_id": tool_call.id,
        "is_error": result.is_error,
        "duration_ms": duration_ms,
    })
    return result
