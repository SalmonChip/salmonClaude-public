"""传输层协议模型：双进程之间说的"话"的契约与 NDJSON 分帧。

消息分三类，靠顶层 type 字段区分：
- request  : 客户端 → 服务端，必有 id，服务端回一条同 id 的 response
- response : 服务端 → 客户端，request 的回执
- event    : 服务端 → 客户端，单向推送，无响应

线上格式是 NDJSON（Newline-Delimited JSON）：每条消息一行 JSON，以 \\n 结尾。
TCP 是字节流、没有消息边界，所以用换行符自定边界；单帧有大小上限，超限即断。
"""

import json
from typing import Any

# 单帧字节数上限（1MB）。超过直接拒绝/断开，防止恶意或异常消息撑爆内存。
MAX_FRAME_SIZE = 1024 * 1024

# 顶层 type 字段的三个取值
TYPE_REQUEST = "request"
TYPE_RESPONSE = "response"
TYPE_EVENT = "event"

# request 的 method 字段取值（本阶段八种操作）
METHOD_PING = "ping"
METHOD_SUBSCRIBE = "subscribe"
METHOD_START_RUN = "start_run"
METHOD_RESPOND = "respond"
METHOD_SESSION_LIST = "session.list"
METHOD_SESSION_GET = "session.get"
METHOD_SESSION_ADD_NOTE = "session.add_note"
METHOD_SESSION_COMPACT = "session.compact"




class ProtocolError(ValueError):
    """协议层错误：JSON 解析失败、帧超长、顶层非对象等，读循环据此断开连接。"""


def encode_message(message: dict[str, Any]) -> bytes:
    """把一个消息编码成一行 NDJSON 帧。

    输入：message（消息 dict）
    输出：json.dumps 后拼 \\n 的字节串，可直接写进 socket
    功能：序列化并加换行分帧；结果超 MAX_FRAME_SIZE 抛 ProtocolError
    """
    raw = json.dumps(message, ensure_ascii=False).encode("utf-8") + b"\n"
    if len(raw) > MAX_FRAME_SIZE:
        raise ProtocolError(f"帧过大: {len(raw)} 字节，上限 {MAX_FRAME_SIZE}")
    return raw


def parse_message(line: bytes) -> dict[str, Any]:
    """把一行 NDJSON 帧解析成消息 dict。

    输入：line（读循环读到的一行原始字节，末尾换行可保留）
    输出：解析出的消息 dict
    功能：先校验帧大小，再 json.loads；超长 / 坏 JSON / 顶层非对象 抛 ProtocolError
    """
    if len(line) > MAX_FRAME_SIZE:
        raise ProtocolError(f"帧过大: {len(line)} 字节，上限 {MAX_FRAME_SIZE}")
    try:
        obj = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"非法 JSON 帧: {exc}") from exc
    if not isinstance(obj, dict):
        raise ProtocolError(f"消息必须是 JSON 对象，收到 {type(obj).__name__}")
    return obj


def make_request(request_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """构造一条请求消息。

    输入：request_id（唯一 id，回执按它配对）、method（操作名）、params（参数）
    输出：{"type": "request", "id": ..., "method": ..., "params": ...}
    功能：统一请求的线上形状，避免各处手拼 dict 拼错
    """
    return {"type": TYPE_REQUEST, "id": request_id, "method": method, "params": params}


def make_response(request_id: str, result: dict[str, Any] | None = None, error: str | None = None) -> dict[str, Any]:
    """构造一条响应消息（成功带 result，失败带 error，二者互斥）。

    输入：request_id（对应请求的 id）、result（成功结果）、error（失败信息）
    输出：{"type": "response", "id": ..., "ok": ..., "result"/"error": ...}
    功能：统一响应的线上形状；ok 由 error 是否为 None 推出
    """
    message: dict[str, Any] = {"type": TYPE_RESPONSE, "id": request_id, "ok": error is None}
    if error is None:
        message["result"] = result if result is not None else {}
    else:
        message["error"] = error
    return message


def make_event(event_type: str, run_id: str, data: dict[str, Any]) -> dict[str, Any]:
    """构造一条事件消息（服务端单向推给客户端）。

    输入：event_type（事件名，如 text_delta / permission.requested）、run_id、data（事件负载）
    输出：{"type": "event", "event": ..., "run_id": ..., "data": ...}
    功能：统一事件的线上形状
    """
    return {"type": TYPE_EVENT, "event": event_type, "run_id": run_id, "data": data}
