"""salmon-core 的启动横幅：Spring Boot 风格的彩色 banner + 启动日志。

只做展示，不碰业务逻辑。daemon_cli 在起服务前后调用：
起之前打印 banner + 配置行；起之后打印监听地址 + 耗时（类比 Spring Boot 的
「Started XxxApplication in N seconds」）。

颜色用 rich（Textual 的依赖，随项目已装），它在 Windows 上会正确处理 ANSI 转义。
"""

import time
from datetime import datetime

from rich.console import Console
from rich.panel import Panel
from rich.text import Text

_console = Console()

# 鱼的 ASCII 符号：><(((('>（气泡 + 鱼身 + 尾巴），一排 = salmon 主题
_SALMON = "><(((('>"

_COLOR_FISH = "cyan"        # 鱼群
_COLOR_TITLE = "orange1"    # 项目名（salmon 的橙）
_COLOR_SUB = "yellow"       # 副标题


def print_banner(version: str) -> None:
    """打印顶部横幅：鱼群 + 项目名 + 版本 + 副标题。

    谁调用：daemon_cli.main（起服务前）
    输入：version（版本号字符串，如 "0.1.0"）
    输出：无（副作用：往控制台打印一个彩色 Panel）
    功能：用 rich 的 Panel 把 ASCII 鱼群、项目名、版本、一句话副标题包成一个横幅。
    """
    title = Text()
    title.append("   ".join([_SALMON] * 3) + "\n", style=_COLOR_FISH)
    title.append("salmonClaude", style=f"bold {_COLOR_TITLE}")
    title.append(f"  v{version}\n", style="dim")
    title.append("AI 智能体框架 · 双进程 (core + tui)", style=_COLOR_SUB)
    _console.print(Panel(title, border_style=_COLOR_FISH, expand=False))


def _log(message: str) -> None:
    """打印一行 Spring Boot 风格日志：时间戳 + 绿色 INFO + 消息。

    输入：message（已含 rich 标记的日志正文）
    输出：无
    功能：拼「YYYY-MM-DD HH:MM:SS  INFO  消息」并打印。
    """
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    _console.print(f"[dim]{ts}[/]  [green]INFO[/]  {message}")


def print_startup_info(model: str, base_url: str, workspace: str, is_demo: bool) -> None:
    """打印启动信息：模型 / base_url / workspace（demo 模式则提示假模型）。

    谁调用：daemon_cli.main（起服务前）
    输入：model（模型名）、base_url（接口地址）、workspace（工作目录）、is_demo（是否 --demo）
    输出：无
    功能：逐行打印启动配置；demo 模式 model 行替换成假模型提示，不误导「在连真模型」。
    """
    _log("salmon-core 启动中...")
    if is_demo:
        _log("model      : [bold yellow](demo 假模型，不联网)[/]")
    else:
        _log(f"model      : [bold]{model}[/]")
        _log(f"base_url   : [bold]{base_url}[/]")
    _log(f"workspace  : [bold]{workspace}[/]")


def print_listening(host: str, port: int, started_at: float) -> None:
    """打印监听地址 + 启动耗时（服务真正起来后调用）。

    谁调用：daemon_cli._serve（app.start() 之后，此时端口已确定）
    输入：host（监听地址）、port（真实端口）、started_at（time.monotonic 起始值）
    输出：无
    功能：打印 listening 行和「启动完成，耗时 N 秒」，收尾整个启动日志。
    """
    _log(f"listening  : [bold]{host}:{port}[/]")
    elapsed = time.monotonic() - started_at
    _log(f"salmon-core 启动完成，耗时 [bold green]{elapsed:.3f}s[/]")
