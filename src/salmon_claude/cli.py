import argparse
import asyncio
import shlex
from pathlib import Path
from typing import Any

from salmon_claude import __version__
from salmon_claude.config import get_config
from salmon_claude.core.runner import AgentRunner
from salmon_claude.llm.openai_compat import OpenAICompatProvider
from salmon_claude.mcp.adapter import build_registry as build_mcp_registry
from salmon_claude.mcp.client import McpClient
from salmon_claude.skills import SKILLS, apply_skill
from salmon_claude.tools.builtin.list_dir import ListDirTool
from salmon_claude.tools.builtin.read_file import ReadFileTool
from salmon_claude.tools.builtin.search_files import SearchFilesTool
from salmon_claude.tools.builtin.write_file import WriteFileTool
from salmon_claude.tools.registry import ToolRegistry


def build_registry(workspace_root: Path) -> ToolRegistry:
    """注册内置工具（read_file / list_dir / search_files / write_file），统一绑定 workspace_root。"""
    registry = ToolRegistry()
    registry.register(ReadFileTool(workspace_root=workspace_root))
    registry.register(ListDirTool(workspace_root=workspace_root))
    registry.register(SearchFilesTool(workspace_root=workspace_root))
    registry.register(WriteFileTool(workspace_root=workspace_root))
    return registry


async def prompt_approve(data: dict[str, Any]) -> bool:
    """把一次写请求展示给用户，读入 y/n 决定。

    输入：data（permission.requested 事件里的 data，含 tool_name 和 params）
    输出：True=允许，False=拒绝
    功能：打印工具名 + 写路径 + 内容摘要，用 asyncio.to_thread(input) 读用户输入。
    """
    tool_name = data["tool_name"]
    params = data.get("params", {})
    path = params.get("path")
    content = params.get("content", "")
    summary = content[:80] + ("..." if len(content) > 80 else "")

    print(f"\n[审批] 工具 {tool_name} 请求写入：")
    print(f"  路径: {path}")
    print(f"  内容摘要: {summary}")

    answer = await asyncio.to_thread(input, "  允许吗？(y/n): ")
    return answer.strip().lower() in ("y", "yes", "1")


async def run_agent(goal: str, skill_name: str | None = None, mcp_command: str | None = None) -> None:
    """装配真模型 + 工具 + Runner，跑一次 run 并打印结果。

    输入：goal（任务描述）、skill_name（可选技能名，None=不用技能）、
        mcp_command（可选 MCP server 启动命令字符串，None=不接 MCP）
    输出：无（打印 status/result/run_id）
    功能：先 build_registry 得默认工具表；给了 mcp_command 就起 MCP 子进程、
        build_mcp_registry 后 merge 进工具表；给了技能最后 apply_skill 把 goal 换成
        渲染提示词、registry 换成白名单表——**先汇总（内置+MCP）再统一过滤**，
        MCP 工具同样受技能白名单约束，不会被 review 之类只读技能漏放；
        run 完 finally 里关掉 MCP 子进程，避免泄漏
    """
    config = get_config()
    registry = build_registry(config.workspace_root)


    mcp_client: McpClient | None = None
    if mcp_command:
        mcp_client = McpClient(shlex.split(mcp_command))
        await mcp_client.start()
        registry.merge(await build_mcp_registry(mcp_client))

    if skill_name:
        skill = SKILLS[skill_name]
        goal, registry = apply_skill(skill, registry, goal)

    provider = OpenAICompatProvider(config, registry.tool_schemas())
    runner = AgentRunner(provider, registry, config.workspace_root, approve=prompt_approve)
    try:
        context = await runner.run_and_capture(goal, config.max_steps)
    finally:
        if mcp_client is not None:
            await mcp_client.close()

    print(f"status: {context.status}")
    print(f"result: {context.result}")
    if context.reason:
        print(f"reason: {context.reason}")
    print(f"run_id: {context.run_id}")
    



def main() -> None:
    parser = argparse.ArgumentParser(prog="salmon")
    parser.add_argument("--version", action="version", version=f"salmon {__version__}")
    # 交互模式（不带子命令时生效）的顶层选项
    parser.add_argument("--host", default="127.0.0.1", help="daemon 监听地址")
    parser.add_argument("--port", type=int, default=0, help="监听端口，0=自动挑空闲")
    parser.add_argument("--workspace", default=None, help="工作目录，缺省用 SALMON_WORKSPACE")
    parser.add_argument("--demo", action="store_true", help="用假模型演示，不联网、不需要 key")

    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="跑一次任务")
    run_parser.add_argument("--goal", required=True, help="任务描述")
    run_parser.add_argument("--skill", default=None, help="可选技能名，如 review")
    run_parser.add_argument("--mcp", default=None, help="可选 MCP server 启动命令，如 'python -m salmon_claude.mcp.fake_server'")
    args = parser.parse_args()

    if args.command == "run":
        asyncio.run(run_agent(args.goal, args.skill, args.mcp))
    else:
        # 一条命令起 core + tui（类似 claude）；lazy import 避免 run 分支付 Textual 启动开销
        from salmon_claude.launcher import launch_interactive

        launch_interactive(args.host, args.port, args.workspace, args.demo)


if __name__ == "__main__":
    main()
