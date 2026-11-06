import json
from typing import Any

import httpx

from salmon_claude.config import Config
from salmon_claude.llm.base import TokenCallback
from salmon_claude.llm.types import LlmResponse, ToolCallBlock, UsageStats


class OpenAICompatProvider:
    """调用 OpenAI 兼容接口（DeepSeek/Qwen）的真实模型；负责 Anthropic↔OpenAI 的翻译。"""

    def __init__(self, config: Config, tools: list[dict[str, Any]]) -> None:
        """保存配置和工具定义。

        输入：config（模型名/base_url/api_key 都在里面）、tools（registry.tool_schemas() 的结果）
        输出：无
        功能：把 config 的 model/base_url/api_key 和 tools 存成实例字段，供 chat() 使用。
        """
        self.config = config
        self.tools = tools

    def _to_openai_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把内部 Anthropic 风格消息翻译成 OpenAI 风格，保持 tool_use/tool_result 的 id 配对。

        输入：内部 messages（assistant 里是 tool_use、user 里是 tool_result）
        输出：OpenAI 接口能吃的 messages（assistant 用 tool_calls、工具结果用 role="tool"）
        功能：纯翻译，不碰网络；tool_use 的 input（dict）要 json.dumps 成 arguments 字符串。
        """
        out = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if role == "assistant" and isinstance(content, list):
                # 分支 A：assistant 带工具调用 —— 拆 text + tool_calls
                text_parts = []
                tool_calls = []

                for block in content:
                    if block["type"] == "text":
                        text_parts.append(block["text"])
                    elif block["type"] == "tool_use":
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": json.dumps(block["input"]),   # 关键：dict → JSON 字符串
                            },
                        })

                new_msg: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
                if tool_calls:
                    new_msg["tool_calls"] = tool_calls
                out.append(new_msg)
            elif role == "user" and isinstance(content, list):
                # 分支 B：user 的 block 列表 —— 工具结果转 role="tool"，文本块转 user 文本
                text_parts: list[str] = []
                for block in content:
                    if block.get("type") == "tool_result":
                        out.append({
                            "role": "tool",
                            "tool_call_id": block["tool_use_id"],   # 注意：tool_use_id → tool_call_id
                            "content": block["content"],
                        })
                    elif block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                if text_parts:
                    out.append({"role": "user", "content": "\n".join(text_parts)})
            else:
                # 分支 C：content 是字符串 —— 原样保留
                out.append(msg)
        return out

    def _to_openai_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """把 registry.tool_schemas() 的工具定义翻译成 OpenAI tools 格式。
        输入：内部工具定义列表（每个是 {"name","description","input_schema"}）
        输出：OpenAI tools（每个是 {"type":"function","function":{"name","description","parameters"}}）
        功能：纯翻译；input_schema 直接搬过去当成 parameters，只套一层 function 壳。
        """
        out = []
        for tool in tools:
            out.append({
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["input_schema"],
                },
            })
        return out

    async def chat(self, messages: list[dict[str, Any]], on_token: TokenCallback | None = None) -> LlmResponse:
        """给真实模型发一轮消息，返回完整响应；给了 on_token 就走流式。

        输入：内部 Anthropic 风格 messages、on_token（可选，逐 chunk 回调文本）
        输出：LlmResponse（stop_reason / text / tool_calls / usage 都填好）
        功能：① 检查 api_key ② 翻译 messages+tools ③ 发 HTTP 到 base_url/chat/completions
            ④ 把 OpenAI 响应翻译回 LlmResponse。on_token 非 None 时改 stream=True 逐字回调；
            网络失败不重试、直接抛，交给上层当 llm_error。
        """
        api_key = self.config.api_key
        if not api_key:
            raise RuntimeError("缺少 SALMON_API_KEY 环境变量，无法调用真实模型")

        url = f"{self.config.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.config.model,
            "messages": self._to_openai_messages(messages),
        }
        tools = self._to_openai_tools(self.tools)
        if tools:
            body["tools"] = tools

        if on_token is not None:
            body["stream"] = True
            body["stream_options"] = {"include_usage": True}   # 流式也回 usage
            return await self._chat_stream(url, headers, body, on_token)
        return await self._chat_once(url, headers, body)

    async def _chat_once(self, url: str, headers: dict[str, str], body: dict[str, Any]) -> LlmResponse:
        """一次性请求：发完整 body，解析非流式响应。

        输入：url、headers、body
        输出：LlmResponse
        功能：post → raise_for_status → 解析 choices[0]，含 usage 回填。
        """
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            data = resp.json()

        choice = data["choices"][0]
        message = choice["message"]
        finish_reason = choice.get("finish_reason")
        return self._parse_message(message, finish_reason, data.get("usage"))

    async def _chat_stream(self, url: str, headers: dict[str, str], body: dict[str, Any], on_token: TokenCallback) -> LlmResponse:
        """流式请求：逐 SSE chunk 解析，边收边回调 on_token。

        输入：url、headers、body、on_token（每吐一个文本 delta 回调一次）
        输出：LlmResponse（文本 = 全部 delta 拼接；tool_calls 按 index 累积；usage 来自末尾 chunk）
        功能：client.stream POST → aiter_lines 逐行解析 data: 帧；[DONE] 结束；
            delta.content 追加文本并回调；delta.tool_calls 按 index 累积 id/name/arguments。
        """
        text_parts: list[str] = []
        tool_call_parts: dict[int, dict[str, str]] = {}   # index → {"id","name","arguments"}
        finish_reason: str | None = None
        usage: dict[str, Any] | None = None

        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("POST", url, headers=headers, json=body) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    payload = line[len("data:"):].strip()
                    if payload == "[DONE]":
                        break
                    data = json.loads(payload)
                    if data.get("usage"):
                        usage = data["usage"]
                    choice = data.get("choices", [{}])[0]
                    delta = choice.get("delta", {})
                    if delta.get("content"):
                        text_parts.append(delta["content"])
                        await on_token(delta["content"])
                    for tc in delta.get("tool_calls") or []:
                        idx = tc.get("index", 0)
                        slot = tool_call_parts.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                        if tc.get("id"):
                            slot["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            slot["name"] = fn["name"]
                        if fn.get("arguments"):
                            slot["arguments"] += fn["arguments"]
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]

        # 把累积的 tool_call 碎片还原成 ToolCallBlock
        tool_calls: list[ToolCallBlock] = []
        for idx in sorted(tool_call_parts):
            slot = tool_call_parts[idx]
            arguments = slot["arguments"] or "{}"
            tool_calls.append(ToolCallBlock(
                id=slot["id"],
                name=slot["name"],
                input=json.loads(arguments),
            ))

        return self._parse_message(
            {"content": "".join(text_parts) or None, "tool_calls": None},
            finish_reason,
            usage,
            tool_calls=tool_calls,
        )

    def _parse_message(
        self,
        message: dict[str, Any],
        finish_reason: str | None,
        usage: dict[str, Any] | None,
        tool_calls: list[ToolCallBlock] | None = None,
    ) -> LlmResponse:
        """把 OpenAI 的 message/finish_reason/usage 翻译回 LlmResponse。

        输入：message（content/tool_calls）、finish_reason、usage、tool_calls（流式已还原好的）
        输出：LlmResponse
        功能：finish_reason 映射 stop_reason；tool_calls（非流式）解析成 ToolCallBlock；
            usage 回填成 UsageStats。
        """
        stop_reason = {
            "stop": "end_turn",
            "tool_calls": "tool_use",
            "length": "max_tokens",
        }.get(finish_reason, "end_turn")

        text = message.get("content") or ""    # 纯工具调用时 content 可能是 None

        if tool_calls is None:
            tool_calls = []
            for tc in message.get("tool_calls", []) or []:
                fn = tc["function"]
                tool_calls.append(ToolCallBlock(
                    id=tc["id"],
                    name=fn["name"],
                    input=json.loads(fn["arguments"]),   # 反向：JSON 字符串 parse 回 dict
                ))

        usage_stats: UsageStats | None = None
        if usage:
            usage_stats = UsageStats(
                input_tokens=usage.get("prompt_tokens", 0),
                output_tokens=usage.get("completion_tokens", 0),
            )

        return LlmResponse(stop_reason=stop_reason, text=text, tool_calls=tool_calls, usage=usage_stats)
