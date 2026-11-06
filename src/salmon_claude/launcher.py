"""一条命令同时起 salmon-core + salmon-tui（类似 claude）。

流程：挑一个空闲端口 → 打印启动横幅 → 起 core 子进程（stdout/stderr 吞进 DEVNULL，
横幅由本进程负责，避免 core 的日志污染 TUI 终端）→ 轮询等 core 监听 →
在本进程跑 Textual TUI（阻塞）→ TUI 退出后收掉 core 子进程。

为什么 core 的输出要吞掉：core 和 TUI 现在共用同一个终端，TUI 用备用屏幕渲染，
core 若再往这个终端 print（错误日志等）会打花 TUI 画面。横幅改由 launcher 打。
"""

import asyncio
import socket
import subprocess
import sys
import time
from pathlib import Path

from salmon_claude import __version__
from salmon_claude.config import get_config
from salmon_claude.startup import print_banner, print_listening, print_startup_info
from salmon_claude.tui.app import SalmonTui


def _pick_free_port(host: str) -> int:
    """bind host:0 让系统挑一个空闲端口，随即释放。

    谁调用：launch_interactive（port==0 时）
    输入：host（监听地址）
    输出：int（空闲端口）
    功能：socket bind(0) → getsockname 拿端口 → with 退出自动 close。
        有微小 TOCTOU 竞态（拿到后、core 绑定前被别人占），本地开发可接受。
    """
    with socket.socket() as s:
        s.bind((host, 0))
        return s.getsockname()[1]


async def _wait_ready(host: str, port: int, proc: subprocess.Popen, timeout: float = 10.0) -> None:
    """轮询直到 core 开始监听；core 提前退出（崩溃）或超时就抛 RuntimeError。

    谁调用：launch_interactive
    输入：host / port（探测地址）、proc（core 子进程，用于检测提前退出）、timeout（秒）
    输出：无
    功能：每轮先看 proc 是否已退（崩溃则立刻报错），再 open_connection 探测，
        连上就关掉探测连接并返回（server 对「连上即断」走 EOF 分支，不会崩）；
        超时抛错并提示单独跑 salmon-core 排查。
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"salmon-core 提前退出（退出码 {proc.returncode}）")
        try:
            _, writer = await asyncio.open_connection(host, port)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.1)
    raise RuntimeError(f"等待 salmon-core 监听超时（{timeout}s）")


def launch_interactive(host: str, port: int, workspace: str | None, demo: bool) -> None:
    """一条命令跑完整交互：起 core 子进程 + 跑 TUI，退出时一起收。

    谁调用：cli.main 的默认分支（不带 run 子命令时）
    输入：host（监听地址）、port（0=自动挑空闲）、workspace（None=用 .env）、demo（是否假模型）
    输出：无
    功能：① 挑端口 ② 打印横幅（core 输出被吞，横幅由这里打）③ Popen 起 core
        ④ 轮询等就绪 ⑤ 本进程跑 SalmonTui（阻塞到退出）⑥ finally 收掉 core。
    """
    config = get_config()
    if port == 0:
        port = _pick_free_port(host)
    workspace_root = Path(workspace) if workspace else config.workspace_root

    print_banner(__version__)
    print_startup_info(config.model, config.base_url, str(workspace_root), demo)

    cmd = [
        sys.executable, "-m", "salmon_claude.daemon_cli",
        "--host", host,
        "--port", str(port),
        "--workspace", str(workspace_root),
    ]
    if demo:
        cmd.append("--demo")

    started_at = time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        asyncio.run(_wait_ready(host, port, proc, timeout=10.0))
        print_listening(host, port, started_at)
        SalmonTui(host, port).run()
    except RuntimeError as exc:
        print(f"[错误] {exc} —— 可用 `salmon-core --port {port}` 单独起服务排查")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
