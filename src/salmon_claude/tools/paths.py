"""工具路径校验：所有文件/目录工具统一用它把相对路径解析到 workspace 内。"""

from pathlib import Path


def resolve_in_workspace(path: str, workspace_root: Path) -> Path:
    """把用户给的路径解析到 workspace_root 下，越界/绝对路径抛 PermissionError。

    谁调用：read_file / list_dir / write_file 三个工具的 invoke
    输入：path（用户给的路径，应为相对路径）、workspace_root（工作区根，绝对路径）
    输出：Path（workspace_root 下解析后的真实路径）
    功能：先拒绝对路径（含盘符），再 (workspace_root / path).resolve() 摊平 ..，
        用 is_relative_to 判断是否还在 workspace_root 内；逃出抛 PermissionError。
        读写列三个工具共用这一处，就不会各写各的校验、也不会依赖进程当前目录。
    """
    p = Path(path)
    if p.is_absolute():
        raise PermissionError(f"不允许绝对路径: {path}")
    root = workspace_root.resolve()
    resolved = (workspace_root / p).resolve()
    if not resolved.is_relative_to(root):
        raise PermissionError(f"路径逃出工作目录: {path}")
    return resolved
