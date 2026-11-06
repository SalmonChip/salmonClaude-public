from collections.abc import Awaitable, Callable
from typing import Any

# 处理器类型：一个 async 回调，吃一个事件 dict
Handler = Callable[[dict[str, Any]], Awaitable[None]]


class EventBus:
    """内存广播系统：把事件发给所有订阅的处理器，不落盘、不关心事件内容。"""

    def __init__(self) -> None:
        """初始化一个空的订阅列表。"""
        self._handlers: list[Handler] = []

    def subscribe(self, handler: Handler) -> Callable[[], None]:
        """注册一个处理器，返回一个退订函数。

        输入：handler —— async 回调，接收事件 dict
        输出：退订函数（无参无返回），调用它就把 handler 从列表移除
        功能：追加 handler；退订按 identity 移除，重复订阅不会误删其它处理器。
        """
        self._handlers.append(handler)

        def unsubscribe() -> None:
            self._handlers.remove(handler)

        return unsubscribe

    async def publish(self, event: dict[str, Any]) -> None:
        """把一个事件广播给所有订阅者。

        输入：event —— 带 type / run_id / timestamp / data 四字段的 dict
        输出：无
        功能：逐个 await 每个 handler；单个 handler 抛异常时记下来，继续广播其它 handler。
        """
        for handler in list(self._handlers):   # 复制一份，防止 handler 在广播中途退订
            try:
                await handler(event)
            except Exception as exc:
                # 一个监听者挂掉不该搞崩整个 run，继续发给其它监听者
                print(f"[EventBus] 处理器 {handler!r} 失败: {exc}")
