from pathlib import Path
from typing import Any, ClassVar

from salmon_claude.tools.base import BaseTool, ToolResult
from salmon_claude.tools.paths import resolve_in_workspace

_MAX_BYTES = 512 * 1024  # 512 KB


class ReadFileTool(BaseTool):
    """读取一个文件的文本内容（只读工具）。"""
    name = "read_file"
    description = (
        "读取文件的文本内容。路径相对于工作目录。"
        "超过 512 KB 会被截断。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要读取的文件相对路径。",
            },
        },
        "required": ["path"],
    }

    def __init__(self, workspace_root: Path | None = None) -> None:
        """指定读取边界目录；不传默认当前工作目录。

        输入：workspace_root（可选，所有读取都限制在这个目录内）
        输出：无
        功能：保存 workspace_root；None 时回退成 Path.cwd()。读写列三个工具口径一致。
        """
        self._workspace_root = workspace_root or Path.cwd()

    async def invoke(self, params: dict[str, Any]) -> ToolResult:
        """读取文件文本；超 512KB 截断；绝对路径/越界由统一校验抛 PermissionError。"""
        path_str = params.get("path")
        if not isinstance(path_str, str):
            raise TypeError("read_file 需要 'path' 参数")

        # 统一路径校验：绝对路径、.. 逃逸都在这里拦，返回 workspace 下的真实路径
        target = resolve_in_workspace(path_str, self._workspace_root)

        raw = target.read_bytes()   # 文件不存在 → 抛 FileNotFoundError

        truncated = len(raw) > _MAX_BYTES
        text = raw[:_MAX_BYTES].decode("utf-8", errors="replace")
        if truncated:
            text += "\n[truncated]"

        return ToolResult(content=text)
