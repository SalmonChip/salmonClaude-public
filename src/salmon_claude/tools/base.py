from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass
class ToolResult:
    """一次工具执行的最终结果。"""
    content: str
    is_error: bool = False


class BaseTool(ABC):
    """所有工具必须满足的抽象基类。"""
    name: ClassVar[str]
    description: ClassVar[str]
    input_schema: ClassVar[dict[str, Any]]

    # 写类工具置 True（需要审批），读类工具默认 False
    needs_permission: ClassVar[bool] = False

    @abstractmethod
    async def invoke(self, params: dict[str, Any]) -> ToolResult:
        """执行工具，返回结果或错误。"""
        ...
