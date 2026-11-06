import asyncio
import time
import uuid
from typing import Any

from salmon_claude.core.events.bus import EventBus
from salmon_claude.permissions.policy import Policy


class PermissionManager:
    """把"要不要批准某次写操作"变成一次可等待、可超时、可取消的握手。

    输入：policy（策略）、bus（事件总线）、run_id（本次 run）、timeout（等待批准秒数）
    输出：无
    功能：check_and_wait 按策略决定 allow/deny/ask；ask 时登记 pending Future、
        广播 permission.requested、await 到 respond 或超时；respond 唤醒对应 Future。
    """

    def __init__(self, policy: Policy, bus: EventBus, run_id: str, timeout: float = 300.0) -> None:
        """保存依赖，初始化挂起请求表。

        输入：policy（策略）、bus（事件总线）、run_id（本次 run）、timeout（等待批准秒数）
        输出：无
        功能：四个依赖存成实例字段；_pending 存「request_id -> Future」的挂起表。
        """
        self._policy = policy
        self._bus = bus
        self._run_id = run_id
        self._timeout = timeout
        self._pending: dict[str, asyncio.Future[bool]] = {}

    async def check_and_wait(self, tool_name: str, params: dict[str, Any]) -> bool:
        """决定并等待一次工具调用的审批。

        输入：tool_name（工具名）、params（工具参数）
        输出：True=允许执行，False=拒绝（含超时）
        功能：allow 直接 True；deny 直接 False；ask 则「登记 Future → 广播事件 → await」，
            超时或取消都要在 finally 里把 Future 从 pending 里清掉（不遗留）。
        """
        decision = self._policy.decide(tool_name)
        if decision == "allow":
            return True
        if decision == "deny":
            return False

        # ask：① 登记 Future，② 广播事件，③ 挂起等别人 respond
        request_id = uuid.uuid4().hex
        fut = asyncio.get_running_loop().create_future()
        self._pending[request_id] = fut

        try:
            event = {
                "type": "permission.requested",
                "run_id": self._run_id,
                "timestamp": time.time(),
                "data": {
                    "request_id": request_id,
                    "tool_name": tool_name,
                    "params": params,
                },
            }
            await self._bus.publish(event)
            # wait_for 负责超时：超时会 cancel 掉 fut 并抛 TimeoutError
            return await asyncio.wait_for(fut, timeout=self._timeout)
        except TimeoutError:
            # 没人 respond，当作拒绝
            return False
        finally:
            # 无论超时、被取消、还是正常返回，都把 Fut 从表里摘掉，不遗留
            self._pending.pop(request_id, None)

    def respond(self, request_id: str, allow: bool) -> bool:
        """用审批结果唤醒一个挂起的请求。

        输入：request_id（哪次请求）、allow（是否允许）
        输出：True=成功唤醒；False=没找到或已结束（未知 id 不影响其它请求）
        功能：从 pending 取 Future，没完成就 set_result(allow)。
        """
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(allow)
        return True

    def cancel_all(self) -> None:
        """取消所有挂起的审批请求并清空 pending。

        输入：无；输出：无
        功能：run 收尾时兜底，避免遗留未决 Future。
        """
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
