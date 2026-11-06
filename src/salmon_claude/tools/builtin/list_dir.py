from pathlib import Path
from typing import Any, ClassVar

from salmon_claude.tools.base import BaseTool, ToolResult
from salmon_claude.tools.paths import resolve_in_workspace


class ListDirTool(BaseTool):
    """列出一个目录的内容（只读工具）。"""
    name = "list_dir"
    description = "列出目录的内容。路径相对于工作目录，默认 '.'。"
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "要列出的目录相对路径（默认 '.'）。",
            },
        },
        "required": [],
    }

    def __init__(self, workspace_root: Path | None = None) -> None:
        """指定列出边界目录；不传默认当前工作目录。

        输入：workspace_root（可选，所有列出都限制在这个目录内）
        输出：无
        功能：保存 workspace_root；None 时回退成 Path.cwd()。与 read/write 口径一致。
        """
        self._workspace_root = workspace_root or Path.cwd()

    async def invoke(self, params: dict[str, Any]) -> ToolResult:
        """列出目录条目；目录不存在或不是目录时报错。"""
        path_str = params.get("path") or "."

        # 统一路径校验：绝对路径、.. 逃逸都在这里拦，返回 workspace 下的真实路径
        root = resolve_in_workspace(path_str, self._workspace_root)

        if not root.exists():
            raise FileNotFoundError(f"目录不存在: {path_str}")
        if not root.is_dir():
            raise NotADirectoryError(f"不是目录: {path_str}")

        entries = sorted(root.iterdir(), key=lambda e: e.name)
        lines = [e.name + ("/" if e.is_dir() else "") for e in entries]
        return ToolResult(content="\n".join(lines))
