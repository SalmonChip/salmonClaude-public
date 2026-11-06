import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from salmon_claude.core.context import ExecutionContext
from salmon_claude.core.events.bus import EventBus
from salmon_claude.core.events.writer import EventWriter
from salmon_claude.core.loop import AgentLoop
from salmon_claude.permissions.manager import PermissionManager
from salmon_claude.permissions.policy import Policy
from salmon_claude.tools.truncate import MAX_TOOL_OUTPUT_CHARS


def _make_event(event_type: str, run_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """造一个标准四字段事件，统一 timestamp 的写法。"""
    return {"type": event_type, "run_id": run_id, "timestamp": time.time(), "data": data}


class AgentRunner:
    """装配 provider/registry/loop/bus/writer 成一次完整 run。

    输入：provider（模型）/ registry（工具）/ workspace_root（输出目录根）
    输出：run_and_capture 返回跑完的 ExecutionContext
    功能：生成 run_id → 建输出目录 → 装配 bus+writer → 发布 run_started → 跑 loop → 发布 run_ended → 关闭 writer。
    """

    def __init__(self, provider, registry, workspace_root, approve: Callable[[dict[str, Any]], Awaitable[bool]] | None = None, max_tool_output_chars: int = MAX_TOOL_OUTPUT_CHARS) -> None:
        """...（原注释保留，多一个 approve / max_tool_output_chars）"""
        self._provider = provider
        self._registry = registry
        self._workspace_root = workspace_root
        self._approve = approve   # 可选：async (data) -> bool，人类审批回调
        self._max_tool_output_chars = max_tool_output_chars   # 工具输出进模型视图前的截断上限


    async def run_and_capture(
        self,
        goal: str,
        max_steps: int,
        run_id: str | None = None,
        bus: EventBus | None = None,
        permissions: PermissionManager | None = None,
        history: list[dict[str, Any]] | None = None,
    ) -> ExecutionContext:
        """跑一次完整 run，返回结果上下文。

        输入：goal（任务描述）、max_steps（最大步数）；
            run_id / bus / permissions / history 可选——daemon 传进来复用（history=本 session 历史），CLI 不传则内部自建
        输出：跑完的 ExecutionContext（status/result/reason 已填好）
        功能：可选参数缺省时内部生成（run_id 随机、bus 新建、permissions 按 approve 构建、history 空），
            然后建输出目录 → 装配 writer/loop/context（history 注入 context）→ 发布 run_started
            → 跑 loop（finally 兜底）→ 发布 run_ended → 关闭 writer。
        """
        if run_id is None:
            run_id = uuid.uuid4().hex
        if bus is None:
            bus = EventBus()
        if permissions is None:
            permissions = self._build_permissions(bus, run_id)

        output_dir = self._workspace_root / "runs" / run_id
        output_dir.mkdir(parents=True, exist_ok=True)

        writer = EventWriter(bus, run_id=run_id, output_dir=output_dir)
        context = ExecutionContext(run_id=run_id, goal=goal, max_steps=max_steps, history=history or [])

        loop = AgentLoop(self._provider, self._registry, permissions, max_tool_output_chars=self._max_tool_output_chars, bus=bus)

        await bus.publish(_make_event("run_started", run_id, {"goal": goal, "max_steps": max_steps}))

        try:
            await loop.run(context)
        finally:
            await bus.publish(_make_event(
                "run_ended",
                run_id,
                {"status": context.status, "result": context.result, "reason": context.reason},
            ))
            writer.close()

        return context

    def _build_permissions(self, bus: EventBus, run_id: str) -> PermissionManager | None:
        """装配权限链；没配 approve 回调就返回 None（只读模式，写工具会被拒）。

        输入：bus（事件总线）、run_id（本次 run）
        输出：PermissionManager（已订阅审批回调）；没 approve 则 None
        功能：造 manager，并往 bus 订阅一个 handler：收到 permission.requested
            就调 approve 拿答案，再 respond 唤醒挂起的 check_and_wait。
        """
        if self._approve is None:
            return None

        permissions = PermissionManager(policy=Policy(), bus=bus, run_id=run_id)

        async def on_permission_requested(event: dict[str, Any]) -> None:
            if event.get("type") != "permission.requested":
                return
            data = event["data"]
            allowed = await self._approve(data)
            permissions.respond(data["request_id"], allowed)

        bus.subscribe(on_permission_requested)
        return permissions
