from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExecutionContext:
    """一次 run 的工作记忆与状态。"""

    # ↓ 字段声明（9 个）
    run_id: str
    goal: str
    max_steps: int
    history: list[dict[str, Any]] = field(default_factory=list)
    messages: list[dict[str, Any]] = field(default_factory=list)
    step: int = 0
    status: str = "running"
    reason: str | None = None
    result: str | None = None

    
    def is_done(self) -> bool:
        """status 不再是 "running" 就说明结束了。"""
        return self.status != "running"

    def mark_success(self, result: str) -> None:
        """标记本次 run 成功，并保存最终结果。"""
        self.status = "success"
        self.result = result

    def mark_failed(self, reason: str) -> None:
        """标记本次 run 失败，并保存失败原因。"""
        self.status = "failed"
        self.reason = reason

    def __post_init__(self) -> None:
        """如果 messages 为空，用「历史 + 本条 goal」作为初始消息。"""
        SYSTEM_PROMPT = (
            "你是 salmonClaude，一个读写本地文件、写操作前需人工审批的 AI 智能体，"
            "运行在自研的 salmonClaude 框架上。被问及身份或模型时，说明你是 salmonClaude 智能体，"
            "不要声称自己是 Claude、GPT 或其他具体模型。"
        )

        if not self.messages:
            self.messages = (
                [{"role": "system", "content": SYSTEM_PROMPT}]
                + list(self.history)
                + [{"role": "user", "content": self.goal}]
            )

    def add_assistant_message(self, content: list[dict[str, Any]]) -> None:
        """把模型返回的 content blocks 追加为一条 assistant 消息。"""
        self.messages.append({"role": "assistant", "content": content})

    def add_tool_result(self, tool_use_id: str, content: str, is_error: bool = False) -> None:
        """追加一条工具结果；同一步的多个结果合并进同一条 user 消息。"""
        block: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            block["is_error"] = True

        last = self.messages[-1] if self.messages else None
        if (
            last is not None
            and last["role"] == "user"
            and isinstance(last["content"], list)
            and last["content"]
            and all(b.get("type") == "tool_result" for b in last["content"])
        ):
            last["content"].append(block)
        else:
            self.messages.append({"role": "user", "content": [block]})

