# salmonClaude

从零手写的 AI 智能体框架，用于学习 Agent 的核心原理：Provider 抽象、Agent Loop、工具系统、MCP、技能、会话持久化、事件流与权限审批。

## 启动方式

```bash
# 装依赖
uv sync

# 交互式 TUI（一条命令起 core + TUI）
uv run salmon

# 单次任务
uv run salmon run --goal "读取 README.md 并生成 SUMMARY.md"
```

