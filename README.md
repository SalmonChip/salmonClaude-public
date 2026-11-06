# salmonClaude

一个从零手写的 AI 智能体框架，用于学习 Agent 的核心原理：Provider 抽象、Agent Loop、工具系统、MCP、技能、会话持久化、事件流与权限审批。

## 功能特性

- **LLM Provider 抽象**：业务循环不关心底层模型，假模型（ScriptedProvider / StreamingDemoProvider）与真模型（OpenAICompatProvider）走同一条 Loop。
- **OpenAI 兼容真模型**：接入 DeepSeek / Qwen，内部用 Anthropic 风格消息，由 Provider 层做双向翻译（含 tool_use ↔ tool_calls）。
- **流式输出**：chat 支持 on_token 回调，逐字推送文本；工具调用参数按 index 增量累积还原。
- **Agent Loop**：plan → act → observe 状态机，支持 max_steps、取消、工具失败回填。
- **工具系统**：内置 read_file / list_dir / search_files / write_file，统一参数校验、错误分类与超长输出截断。
- **MCP 接入**：可挂任意 MCP server，工具动态发现后合并进同一张工具表。
- **技能（Skills）**：提示模板 + 工具白名单打包成可复用对象（内置 review，只读工具集）。
- **会话持久化**：SessionStore 落地 sessions/，支持多轮历史、追加笔记与 compact 摘要压缩。
- **事件记录**：EventBus 广播 + EventWriter 落盘 runs/<run_id>/events.jsonl，按 run_id 隔离。
- **权限审批**：写操作需人工 allow / deny / ask；CLI 走终端 y/n，TUI 走 socket respond。

## 三种运行方式

| 命令 | 形态 | 说明 |
|---|---|---|
| `salmon` | 交互式 TUI | 一条命令起 core + TUI（类似 claude），退出时自动收掉子进程 |
| `salmon run --goal ...` | 单次任务 | 装配假/真模型跑一次 run 就退出，结果打到 stdout |
| `salmon-core` | 常驻 daemon | 只监听 socket，等客户端连入、发 start_run |

另有 `salmon-tui` 可单独启动前端（需已有 core 在跑）。

## 目录结构

```
src/salmon_claude/
├── cli.py           # salmon 入口：run 子命令 + 默认走交互式
├── daemon_cli.py    # salmon-core 入口，装配 DaemonApp 常驻
├── launcher.py      # 起 core 子进程 + 跑 TUI 的一条命令封装
├── startup.py       # 启动横幅与配置信息打印
├── config.py        # 环境变量配置
├── core/            # loop / context / runner / app(daemon) / events
├── llm/             # provider 抽象、真假模型、消息类型
├── tools/           # 工具基类、注册表、builtin/ 内置工具、截断
├── mcp/             # MCP client / 工具适配 / 演示 server
├── skills/          # 技能对象、模板渲染与白名单过滤
├── session/         # 会话模型、存储与管理
├── compact/         # 历史消息摘要压缩
├── transport/       # socket 协议、server、client
├── tui/             # Textual 前端
└── permissions/     # 权限策略与审批管理
```

## 快速开始

```bash
# 0）装依赖
uv sync

# 1）不联网、不需要 key：用假模型跑通全流程（含写审批）
uv run salmon --demo

# 2）单次任务
uv run salmon run --goal "读取 README.md 并生成 SUMMARY.md"

# 3）带技能（review = 代码评审，只保留只读工具）
uv run salmon run --goal "评审 src/salmon_claude/core/loop.py" --skill review

# 4）挂 MCP server（演示 server 自带几个假工具）
uv run salmon run --goal "看看有哪些 MCP 工具" --mcp "python -m salmon_claude.mcp.fake_server"

# 5）版本
uv run salmon --version
```

真模型需要在 `.env` 里配 `SALMON_API_KEY`（可参考 `.env.example`）。

## 环境变量

| 变量 | 默认值 | 说明 |
|---|---|---|
| SALMON_MODEL | deepseek-chat | 模型名 |
| SALMON_BASE_URL | https://api.deepseek.com | OpenAI 兼容接口根，代码会拼 `/chat/completions` |
| SALMON_API_KEY | （无） | 密钥，从 .env 或环境读取；缺了只能用 --demo |
| SALMON_MAX_STEPS | 20 | 最大步数（必须为正数） |
| SALMON_WORKSPACE | . | 工作目录，工具路径以此为根 |

## 数据落地

| 路径 | 内容 |
|---|---|
| runs/<run_id>/events.jsonl | 该次 run 的全部事件（含 permission.requested / tool 结果） |
| sessions/ | 会话原始日志（消息按 message_id 递增，支持笔记与 compact 摘要） |

## 审批模型

写类工具调用会先发 `permission.requested` 事件并挂起等待：

- **CLI 单次 run**：终端打印工具名、写入路径、内容摘要，读 y/n。
- **TUI / daemon**：事件广播到前端，前端 `respond`（allow=true/false）按 run_id 路由回对应 PermissionManager 唤醒。
- 客户端断开时，该连接名下的挂起审批会被取消，run 以 cancelled 收尾，不会永久悬挂。

## 开发

```bash
uv run pytest          # 测试（asyncio_mode=auto）
uv run ruff check .    # 静态检查
uv run mypy src        # 类型检查
```
