from dataclasses import dataclass, field
from typing import Any


@dataclass
class UsageStats:
    """一次模型调用消耗的 token 数。"""
    input_tokens: int   # 输入（发出去）消耗的 token
    output_tokens: int  # 输出（模型生成）消耗的 token

    def total(self) -> int:
        """返回输入 + 输出的 token 总和。"""
        return self.input_tokens + self.output_tokens


@dataclass
class ToolCallBlock:
    """模型想要调用的一次工具。"""
    id: str               # 这次调用的唯一标识，之后和执行结果配对
    name: str             # 要调用的工具名，如 "read_file"
    input: dict[str, Any]  # 传给工具的参数，如 {"path": "README.md"}


@dataclass
class LlmResponse:
    """模型的一次完整返回。"""
    stop_reason: str                                  # 必传：为什么停（end_turn / tool_use / max_tokens）
    tool_calls: list[ToolCallBlock] = field(default_factory=list)  # 要调的工具，默认空
    text: str = ""                                    # 纯文本回答，默认空
    usage: UsageStats | None = None                   # token 用量，假模型没有就 None
