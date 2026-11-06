from pathlib import Path
from typing import Any, ClassVar

from salmon_claude.tools.base import BaseTool, ToolResult
from salmon_claude.tools.paths import resolve_in_workspace


class WriteFileTool(BaseTool):
    """把文本内容写入一个文件（写工具，需人工审批）。"""
    name = "write_file"
    description = "写入文件的文本内容。路径相对于工作目录，写入前需人工审批。"
    needs_permission = True
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要写入的文件相对路径。"},
            "content": {"type": "string", "description": "要写入的文本内容。"},
        },
        "required": ["path", "content"],
    }

    def __init__(self, workspace_root: Path | None = None) -> None:
        """指定写入边界目录；不传默认当前工作目录。

        输入：workspace_root（可选，所有写入都限制在这个目录内）
        输出：无
        功能：保存 workspace_root；None 时回退成 Path.cwd()。
        """
        self._workspace_root = workspace_root or Path.cwd()

    async def invoke(self, params: dict[str, Any]) -> ToolResult:
        """校验参数和路径后写入；路径逃出工作目录时报错，不落盘。

        输入：params（含 path 和 content 两个必填字符串）
        输出：ToolResult（成功返回写了多少字节；失败抛异常，由 invoke_tool 转成 is_error）
        功能：① 校验 path/content 类型；② 用统一校验拦绝对路径和 .. 逃逸；
            ③ 写盘；④ 返回写入摘要。
        """
        path_str = params.get("path")
        content = params.get("content")
        if not isinstance(path_str, str):
            raise TypeError("write_file 需要 'path' 参数")
        if not isinstance(content, str):
            raise TypeError("write_file 需要 'content' 参数")

        # 统一路径校验：resolve 摊平 .. 和符号链接，绝对路径（C:/xxx）也会被拦下
        target = resolve_in_workspace(path_str, self._workspace_root)

        target.write_text(content, encoding="utf-8")
        return ToolResult(content=f"wrote {len(content)} chars to {path_str}")
