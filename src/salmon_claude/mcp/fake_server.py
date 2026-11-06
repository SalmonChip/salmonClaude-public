"""假 MCP server：读 stdin 的 JSON-RPC 行、回响应，供 client 测 initialize→tools/list→tools/call。"""

import json
import sys


# 两个假工具，tools/list 时返回
_TOOLS = [
    {
        "name": "echo",
        "description": "原样返回输入的 message。",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}},
            "required": ["message"],
        },
    },
    {
        "name": "add",
        "description": "返回两个整数 a + b 的和。",
        "inputSchema": {
            "type": "object",
            "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
            "required": ["a", "b"],
        },
    },
]


def handle_request(msg: dict) -> dict | None:
    """处理一条 JSON-RPC 请求，返回响应 dict；不是请求（无 method）返回 None。

    输入：msg（一条 JSON-RPC 消息）
    输出：响应 dict（带同 id）；空则 None
    功能：initialize → 回 protocolVersion/serverInfo；tools/list → 回两个假工具；
        tools/call → 按 name 算结果（echo 原样回、add 求和）
    """
    if "method" not in msg:
        return None
    method = msg["method"]
    req_id = msg.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake-mcp", "version": "0.1.0"},
        }
    elif method == "tools/list":
        result = {"tools": _TOOLS}
    elif method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        if name == "echo":
            text = args.get("message", "")
        elif name == "add":
            text = str(int(args["a"]) + int(args["b"]))
        else:
            text = f"unknown tool: {name}"
        result = {"content": [{"type": "text", "text": text}]}
    else:
        return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"method not found: {method}"}}

    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def serve() -> None:
    """读 stdin 每行 JSON，逐条 handle_request，有响应就 print(flush=True)；EOF 退出。

    功能：进入循环前把 stdout 固定成 UTF-8——Windows 上默认用系统编码（GBK），
        不固定会导致 client 读到的中文响应字节无法按 UTF-8 解码。
    """
    sys.stdout.reconfigure(encoding="utf-8")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        resp = handle_request(msg)
        if resp is not None:
            print(json.dumps(resp, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    serve()
