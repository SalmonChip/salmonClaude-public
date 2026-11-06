"""search_files 工具：递归按文件名（glob）或内容关键词搜索，快速定位文件。"""

import fnmatch
from pathlib import Path
from typing import Any, ClassVar

from salmon_claude.tools.base import BaseTool, ToolResult
from salmon_claude.tools.paths import resolve_in_workspace

# 递归搜索时跳过的目录（缓存/虚拟环境/git/运行时数据，避免拖慢又无意义）
_SKIP_DIRS = {
    ".git", ".venv", "venv", "__pycache__", "node_modules",
    ".idea", ".mypy_cache", ".pytest_cache", ".ruff_cache", "runs", "sessions",
}

_MAX_RESULTS = 200            # 最多返回的匹配条数
_MAX_FILE_BYTES = 256 * 1024  # 内容搜索时超过此大小的文件跳过
_MAX_LINES_PER_FILE = 20      # 内容搜索时每文件最多返回的行数


class SearchFilesTool(BaseTool):
    """递归搜索文件：按文件名 glob 或内容关键词查找，返回相对路径（内容命中带 行号:内容）。"""
    name = "search_files"
    description = (
        "递归搜索文件，快速定位路径，不必逐层 list_dir。"
        "可按文件名 glob 模式（pattern，如 '*.py' 或 'config*'）或文件内容关键词（content）查找；"
        "两者都省略会报错。内容搜索返回 '路径:行号: 内容'，文件名搜索只返回路径。"
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "文件名 glob 模式，如 '*.py'。"},
            "content": {"type": "string", "description": "内容关键词，找包含它的文件。"},
            "path": {"type": "string", "description": "起始目录，默认 '.'。"},
        },
        "required": [],
    }

    def __init__(self, workspace_root: Path | None = None) -> None:
        """指定搜索边界目录；不传默认当前工作目录。

        输入：workspace_root（可选，搜索限制在这个目录内）
        输出：无
        功能：保存 workspace_root；None 时回退 Path.cwd()。与 read/list/write 口径一致。
        """
        self._workspace_root = (workspace_root or Path.cwd()).resolve()

    async def invoke(self, params: dict[str, Any]) -> ToolResult:
        """递归搜索并返回匹配结果；pattern 和 content 都缺时报错。"""
        pattern = params.get("pattern")
        content = params.get("content")
        if pattern is None and content is None:
            raise TypeError("search_files 需要 'pattern'（文件名 glob）或 'content'（内容关键词）")

        start = resolve_in_workspace(params.get("path") or ".", self._workspace_root)
        if not start.is_dir():
            raise NotADirectoryError(f"不是目录: {params.get('path') or '.'}")

        lines = self._collect(start, pattern, content)
        if not lines:
            return ToolResult(content="（没有匹配）")
        return ToolResult(content="\n".join(lines[:_MAX_RESULTS]))

    def _collect(self, start: Path, pattern: str | None, content: str | None) -> list[str]:
        """遍历 start 下所有文件，按 pattern（文件名）和/或 content（内容）过滤。"""
        out: list[str] = []
        for p in sorted(start.rglob("*")):
            if not p.is_file() or any(part in _SKIP_DIRS for part in p.parts):
                continue
            rel = p.relative_to(self._workspace_root).as_posix()
            if pattern is not None and not (
                fnmatch.fnmatch(p.name, pattern) or fnmatch.fnmatch(rel, pattern)
            ):
                continue
            if content is None:
                out.append(rel)
            else:
                out.extend(self._grep(p, rel, content))
        return out

    def _grep(self, p: Path, rel: str, content: str) -> list[str]:
        """在单个文件里找含 content 的行，返回 'rel:行号: 内容'（无命中返回空）。"""
        try:
            if p.stat().st_size > _MAX_FILE_BYTES:
                return []
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        hits: list[str] = []
        for i, line in enumerate(text.splitlines(), start=1):
            if content in line:
                hits.append(f"{rel}:{i}: {line.strip()}")
                if len(hits) >= _MAX_LINES_PER_FILE:
                    break
        return hits
