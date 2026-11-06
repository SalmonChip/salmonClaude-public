from pathlib import Path


def matches_outside_cwd(path: Path, cwd: Path) -> bool:
    """判断 path 解析到 cwd 下后，是否逃出了 cwd。

    输入：path（要检查的相对路径）、cwd（工作目录，绝对路径）
    输出：True=逃出（含 .. 或符号链接指向外部），False=仍在 cwd 内
    功能：用 (cwd / path).resolve() 拿到真实路径，再 is_relative_to 判断是否还在 cwd 内。
    """
    # 拼成绝对路径后再 resolve：把 .. 和符号链接都摊平，得到真实位置
    resolved = (cwd / path).resolve()
    # cwd 本身也 resolve 一下，保证两边口径一致（比如盘符大小写、结尾斜杠）
    return not resolved.is_relative_to(cwd.resolve())


class Policy:
    """决定某个工具调用是 allow / deny / ask。

    输入：rules（可选，dict[工具名 -> "allow"/"deny"/"ask"]）
    输出：无
    功能：保存规则表；decide 查表，没显式配置的工具默认 ask。
    """

    def __init__(self, rules: dict[str, str] | None = None) -> None:
        """保存规则表；没传就用空表（一切默认 ask）。

        输入：rules（可选，dict[工具名 -> "allow"/"deny"/"ask"]）
        输出：无
        功能：把 rules 存成实例字段。
        """
        self._rules = rules or {}

    def decide(self, tool_name: str) -> str:
        """返回该工具的三态之一："allow" / "deny" / "ask"。

        输入：tool_name（工具名，如 "write_file"）
        输出："allow"/"deny"/"ask" 之一
        功能：查 rules，缺省返回 "ask"。
        """
        return self._rules.get(tool_name, "ask")
