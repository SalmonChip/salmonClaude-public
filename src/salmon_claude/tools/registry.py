from typing import Any

from salmon_claude.tools.base import BaseTool


class ToolRegistry:
    """按名字查找工具的表。"""

    def __init__(self) -> None:
        """建一个空字典存工具。"""
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """登记一个工具；同名再登记抛 ValueError。"""
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        """按名字查工具，找不到返回 None。"""
        return self._tools.get(name)

    def tool_schemas(self) -> list[dict[str, Any]]:
        """返回所有工具的 Anthropic 格式 schema 列表。"""
        schemas = []
        for tool in self._tools.values():
            schemas.append({
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            })
        return schemas
    def merge(self, other: "ToolRegistry") -> None:
        """把另一个工具表的所有工具并入自己（原地修改）。

        输入：other（要并入的工具表）
        输出：无
        功能：遍历 other 的工具逐个 register；重名抛 ValueError。
            用于把 MCP 工具并入默认工具表。
        """
        for tool in other._tools.values():
            self.register(tool)

    def filter(self, names: list[str] | None) -> "ToolRegistry":
        """按白名单复制一个工具表。

        输入：names（None=全部工具；[]=空表；否则只保留 names 里的工具名）
        输出：ToolRegistry（新表，不动原表）
        功能：技能要用它把「默认工具表」缩到「该技能允许的工具」；
            白名单里名字对不上（tool 不存在）就跳过不报错
        """
        reg = ToolRegistry()
        if names is None:
            names = list(self._tools.keys())
        for name in names:
            tool = self._tools.get(name)
            if tool is not None:
                reg.register(tool)
        return reg

