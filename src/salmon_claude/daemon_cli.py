"""salmon-core 入口：装配 provider/registry/DaemonApp，起守护进程常驻。

与 cli.py 的区别：cli 是单进程跑完一次 run 就退出；salmon-core 起的是常驻 daemon，
只监听 socket 等 TUI 连进来，run 由 TUI 发 start_run 触发。
"""

import argparse
import asyncio
import time
from pathlib import Path

from salmon_claude import __version__
from salmon_claude.cli import build_registry
from salmon_claude.config import get_config
from salmon_claude.core.app import DaemonApp
from salmon_claude.llm.openai_compat import OpenAICompatProvider
from salmon_claude.llm.types import LlmResponse, ToolCallBlock
from salmon_claude.startup import print_banner, print_listening, print_startup_info


def build_demo_provider() -> "StreamingDemoProvider":
    """造演示假模型：第一步 tool_use 写 SUMMARY.md，第二步 end_turn 流式吐一段总结。

    输入：无
    输出：StreamingDemoProvider（两步预设回复）
    功能：不联网，供 --demo 模式跑通「run 开始 → 流式吐字 → 写审批 → 工具完成 → run 结束」全流程
    """
    return StreamingDemoProvider([
        LlmResponse(
            stop_reason="tool_use",
            tool_calls=[
                ToolCallBlock(id="t1", name="write_file", input={"path": "SUMMARY.md", "content": "hello salmon"})
            ],
        ),
        LlmResponse(stop_reason="end_turn", text="已完成：读了 README，把内容摘要写进了 SUMMARY.md，写入前申请了你的审批。"),
    ])


class StreamingDemoProvider:
    """演示流式的假模型：按预设序列回复，但 reply.text 会逐字回调 on_token（模拟真模型边生成边吐字）。

    与 ScriptedProvider 的区别：多实现 on_token——把返回文本拆成单字、每字 await 一下，
    让 TUI 能看到「逐字刷出」的效果，用来验收 Day 2 的流式链路。
    """

    def __init__(self, replies: list[LlmResponse]) -> None:
        """保存预设回复序列（按顺序弹出）。

        输入：replies（预设的 LlmResponse 列表）
        输出：无
        功能：复制一份存起来，供 chat 按顺序返回
        """
        self._replies = list(replies)

    async def chat(self, messages, on_token=None) -> LlmResponse:
        """返回下一条预设回复；给了 on_token 就把 text 逐字回调出来。

        输入：messages（忽略，只记一次调用）、on_token（可选流式回调）
        输出：LlmResponse（预设的下一条）
        功能：弹出下一条；若 on_token 非 None 且有 text，逐字回调并每字睡 50ms 放慢，
            让肉眼能看到流式效果；工具调用那条 text 为空，直接返回。
        """
        reply = self._replies.pop(0)
        if on_token is not None and reply.text:
            for ch in reply.text:
                await on_token(ch)
                await asyncio.sleep(0.05)   # 放慢到每字 50ms，肉眼可辨
        return reply


async def _serve(app: DaemonApp, host: str, started_at: float) -> None:
    """起 daemon 并挂住，直到 Ctrl+C 取消。

    输入：app（已装配的 DaemonApp）、host（打印地址用）、started_at（启动计时起点）
    输出：无
    功能：start() → 打印真实监听地址 + 启动耗时（端口随机时打真实值）→ 永久等待
    """
    await app.start()
    print_listening(host, app.port, started_at)
    await asyncio.Event().wait()   # 永远不会被 set，等价于永久等待；Ctrl+C 时事件循环关闭


def main() -> None:
    """salmon-core 入口：装配 provider + registry + DaemonApp，起 daemon 常驻。

    谁调用：命令行 `salmon-core`
    输入：argv（--host 默认 127.0.0.1、--port 默认 0=随机、--workspace 可选、--demo 开关）
    输出：打印 listening 地址后常驻，Ctrl+C 退出
    功能：读 config → 造 registry → 按 --demo 选假模型或真模型 → 建 DaemonApp → asyncio.run 常驻
    """
    parser = argparse.ArgumentParser(prog="salmon-core")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址")
    parser.add_argument("--port", type=int, default=0, help="监听端口，0=系统随机分配")
    parser.add_argument("--workspace", default=None, help="工作目录，缺省用 SALMON_WORKSPACE")
    parser.add_argument("--demo", action="store_true", help="用假模型演示，不联网、不需要 key")
    args = parser.parse_args()

    config = get_config()
    workspace_root = Path(args.workspace) if args.workspace else config.workspace_root
    registry = build_registry(workspace_root)

    if args.demo:
        provider = build_demo_provider()
    else:
        provider = OpenAICompatProvider(config, registry.tool_schemas())

    # 启动横幅 + 配置日志（监听地址要等 start() 之后才知道，那里再补一行）
    started_at = time.monotonic()
    print_banner(__version__)
    print_startup_info(config.model, config.base_url, str(workspace_root), args.demo)

    app = DaemonApp(provider, registry, workspace_root, host=args.host, port=args.port)
    asyncio.run(_serve(app, args.host, started_at))


if __name__ == "__main__":
    main()
