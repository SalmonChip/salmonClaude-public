"""adapter：把 MCP server 暴露的工具包成 BaseTool，接进现有 ToolRegistry，复用 invoke_tool/权限/事件。"""

from typing import Any

from salmon_claude.mcp.client import McpClient
from salmon_claude.tools.base import BaseTool, ToolResult
from salmon_claude.tools.registry import ToolRegistry


def _extract_text(result: dict[str, Any]) -> str:
    """把 MCP call_tool 结果里的 content 块拼成一段文本。

    输入：result（tools/call 的 result，形如 {"content": [{"type":"text","text":"..."}]}）
    输出：str（所有 text 块按顺序拼起来）
    功能：MCP 的 content 是块列表，这里只抽 text 块，忽略 image 等其他类型
    """
    parts = []
    for block in result.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


class McpTool(BaseTool):
    """一个 MCP 工具的包装：元信息来自 server，invoke 转成 client.call_tool。

    谁调用：adapter.build_registry 注册、ToolRegistry 的 invoke_tool 执行
    输入：client（已 start 的 McpClient）、spec（tools/list 里的一条工具描述）
    输出：无
    功能：把 name/description/input_schema 从 spec 填到实例上；invoke 把参数转给
        client.call_tool，再把返回的 content 拼成文本、isError 透传成 ToolResult.is_error
    """

    # 外部工具没有明确安全分类，默认走审批（fail-safe）：
    # 避免把带写入能力的 MCP 工具当只读工具静默放行
    needs_permission = True

    def __init__(self, client: McpClient, spec: dict[str, Any]) -> None:
        self._client = client
        self.name = spec["name"]
        self.description = spec["description"]
        self.input_schema = spec.get("inputSchema", {"type": "object", "properties": {}})

    async def invoke(self, params: dict[str, Any]) -> ToolResult:
        """调 MCP server 执行这个工具，结果转成 ToolResult。

        输入：params（工具参数）
        输出：ToolResult（content 块拼成的文本；isError 透传）
        功能：client.call_tool(self.name, params) → _extract_text 抽文本；
            server 回 isError 就置 is_error=True
        """
        result = await self._client.call_tool(self.name, params)
        text = _extract_text(result)
        is_error = bool(result.get("isError", False))
        return ToolResult(content=text, is_error=is_error)


async def build_registry(client: McpClient) -> ToolRegistry:
    """连一个 MCP server，把它的工具全部注册进一个新 ToolRegistry。

    输入：client（已 start 的 McpClient）
    输出：ToolRegistry（含该 server 暴露的所有 MCP 工具）
    功能：initialize 握手 → tools/list 拿工具 → 每条 spec 包成 McpTool 注册
    """
    await client.initialize()
    tools = await client.list_tools()
    registry = ToolRegistry()
    for spec in tools:
        registry.register(McpTool(client, spec))
    return registry
