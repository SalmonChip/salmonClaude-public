"""salmon-tui 入口：连 daemon 的 Textual 客户端界面。

与 salmon-core 配对：core 常驻等连接，tui 连上去 subscribe 事件、渲染多轮对话正文、
把权限审批弹成 Modal、把「批准/拒绝」点回成 respond 请求。
多轮对话：每轮 start_run 都带上 session_id 续同一会话；正文直接取自事件（run_ended.result），不回查历史。
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any, ClassVar

from rich.markup import escape
from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, RichLog

from salmon_claude.transport.client import Client


class PermissionRequested(Message):
    """自定义消息：把订阅到的 permission.requested 事件转成 app 消息，跨任务安全弹 Modal。"""

    def __init__(self, run_id: str, data: dict[str, Any]) -> None:
        self.run_id = run_id
        self.data = data
        super().__init__()


class PermissionModal(ModalScreen[bool]):
    """权限审批弹窗：展示将执行的工具，点批准/拒绝返回 True/False。"""

    def __init__(self, data: dict[str, Any]) -> None:
        super().__init__()
        self.data = data

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("需要你的审批", id="perm-title")
            yield Label(self._summary(), id="perm-detail")
            with Horizontal():
                yield Button("批准", id="approve")
                yield Button("拒绝", id="deny")

    def _summary(self) -> str:
        """把 data 压成一行可读摘要。"""
        tool = self.data.get("tool_name", "?")
        params = self.data.get("params", {})
        return f"工具 {tool} 要写入 {params.get('path', '?')}"

    @on(Button.Pressed, "#approve")
    def _approve(self) -> None:
        """点批准：dismiss(True)。"""
        self.dismiss(True)

    @on(Button.Pressed, "#deny")
    def _deny(self) -> None:
        """点拒绝：dismiss(False)。"""
        self.dismiss(False)


class SessionListModal(ModalScreen[str | None]):
    """会话列表弹窗：DataTable 列历史会话（名称/消息数/更新时间），选中一行返回其 session_id，点新建返回 None。"""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "dismiss", "取消", show=False)]

    def __init__(self, sessions: list[dict[str, Any]]) -> None:
        super().__init__()
        self._sessions = sessions

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("历史会话（↑↓ 选择，Enter 继续，Esc 取消）", id="session-title")
            if not self._sessions:
                yield Label("（还没有历史会话）", id="session-empty")
            else:
                yield DataTable(id="session-table", cursor_type="row")
            yield Button("＋ 新建会话", id="session-new")

    def on_mount(self) -> None:
        """有会话时填表：三列（会话/消息数/更新时间），行号即 sessions 下标，供选中时反查。"""
        if not self._sessions:
            return
        table = self.query_one("#session-table", DataTable)
        table.add_columns("会话", "消息数", "更新时间")
        for s in self._sessions:
            table.add_row(
                s.get("name", ""),
                str(s.get("message_count", 0)),
                self._format_time(s.get("updated_at", 0)),
            )

    def _format_time(self, updated_at: float) -> str:
        """把 epoch 秒转成本地 'MM-DD HH:MM'；0 或非法值返回空串。"""
        if not updated_at:
            return ""
        try:
            return datetime.fromtimestamp(updated_at, tz=UTC).astimezone().strftime("%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            return ""

    @on(DataTable.RowSelected)
    def _on_row_selected(self, event: DataTable.RowSelected) -> None:
        """选中某行：用行号（cursor_row）反查 session 并 dismiss。"""
        if 0 <= event.cursor_row < len(self._sessions):
            self.dismiss(self._sessions[event.cursor_row].get("session_id"))

    @on(Button.Pressed, "#session-new")
    def _on_new(self) -> None:
        """点「新建会话」：dismiss(None)。"""
        self.dismiss(None)


class SalmonTui(App):
    """最小 TUI：连 daemon、订阅事件、渲染多轮对话、弹权限审批、切会话。"""

    TITLE = "salmonClaude"
    SUB_TITLE = "双进程 AI 智能体"

    # 配色（Catppuccin Mocha 深色底 + 暖色强调，和终端 cm 配色协调）
    _DIM = "#6C7086"     # 灰：空闲 / 次文字 / 分隔线 / 工具行
    _ACCENT = "#FAB387"  # 桃橙：唯一强调（用户前缀）
    _OK = "#A6E3A1"      # 绿：已连接 / 助手 / 批准
    _BUSY = "#F9E2AF"    # 金黄：运行中
    _ERR = "#F38BA8"     # 红：失败 / 拒绝

    _SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"   # 运行中旋转指示帧

    CSS = """
    Screen {
        background: #1E1E2E;
        color: #CDD6F4;
    }

    /* 顶部标题栏 / 底部按键提示：跟深色主题一致 */
    Header {
        background: #181825;
        color: #CDD6F4;
    }
    Header .header--title {
        color: #CDD6F4;
        text-style: bold;
    }
    Footer {
        background: #181825;
        color: #6C7086;
    }
    Footer .footer--key {
        color: #FAB387;
    }

    /* 按钮扁平化：透明底、悬停微亮、焦点 accent 文字；主按钮/批准/拒绝只上文字色，不填底 */
    Button {
        background: transparent;
        border: none;
        color: #CDD6F4;
        height: 1;
    }
    Button:hover { background: #313244; }
    Button:focus { color: #FAB387; text-style: bold; }
    #new-session, #session-new { color: #FAB387; }
    #approve { color: #A6E3A1; }
    #deny { color: #F38BA8; }

    #body {
        height: 1fr;
        padding: 0 1 1 1;
    }

    #session-bar {
        height: 3;
        margin-bottom: 1;
        padding: 0 1;
        background: #181825;
        border: round #313244;
        border-title-color: #6C7086;
    }

    #session-label {
        width: 1fr;
        color: #FAB387;
        text-style: bold;
    }

    #log {
        height: 1fr;
        padding: 0 1;
        background: #11111B;
        border: round #313244;
        border-title-color: #6C7086;
    }

    #stream {
        height: auto;
        margin-top: 1;
        padding: 0 1;
        background: #11111B;
        color: #A6E3A1;
        border: round #313244;
        border-title-color: #6C7086;
        display: none;
    }

    #status-bar {
        height: 1;
        margin-top: 1;
        padding: 0 1;
        background: #181825;
        color: #6C7086;
    }

    #status {
        width: 1fr;
    }

    #spinner {
        width: 2;
        color: #F9E2AF;
    }

    #usage {
        color: #6C7086;
    }

    #goal-input {
        margin-top: 1;
        background: #11111B;
        border: round #FAB387;
    }

    #goal-input:focus {
        border: double #A6E3A1;
    }

    /* 会话列表表格 */
    #session-table {
        width: 60;
        height: auto;
        max-height: 20;
        border: round #313244;
        background: #11111B;
    }
    #session-title {
        color: #FAB387;
        text-style: bold;
    }
    """

    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self._host = host
        self._port = port
        self._client: Client | None = None
        self._session_id: str | None = None
        self._stream_buf = ""     # 正在流式生成的助手回复
        self._total_in = 0        # 本轮 run 累计输入 token
        self._total_out = 0       # 本轮 run 累计输出 token
        self._spinner_idx = 0         # 旋转指示当前帧下标
        self._spinner_timer = None    # set_interval 的 Timer，None=没在转

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="body"):
            with Horizontal(id="session-bar"):
                yield Label("会话: 新会话", id="session-label")
                yield Button("新建会话", id="new-session")
                yield Button("切换会话", id="switch-session")
            yield Button("压缩历史", id="compact")
            yield RichLog(id="log", markup=True, wrap=True)
            yield Label("", id="stream")
            with Horizontal(id="status-bar"):
                yield Label("", id="spinner")
                yield Label("未连接", id="status")
                yield Label("", id="usage")
            yield Input(placeholder="输入消息，回车发送", id="goal-input")
        yield Footer()

    async def on_mount(self) -> None:
        """连接 daemon、订阅全部事件、挂事件 handler，状态切「已连接」。"""
        self._client = await Client.connect(self._host, self._port)
        self._client.set_event_handler(self._on_event)
        await self._client.subscribe(None)
        # 面板边框标题（on_mount 在首次渲染前，赋值会被首帧拾取）
        self.query_one("#session-bar", Horizontal).border_title = "会话"
        self.query_one("#log", RichLog).border_title = "对话"
        self.query_one("#stream", Label).border_title = "助手回复"
        self._set_status("已连接", self._OK)

    async def _on_event(self, event: dict[str, Any]) -> None:
        """把一条 daemon 事件渲染进 UI（跑在读循环里，只做同步 UI + post_message，不 await 请求）。"""
        etype = event.get("event")
        data = event.get("data", {})
        if etype == "run_started":
            self._stream_buf = ""
            self._total_in = 0
            self._total_out = 0
            self._set_stream("")
            self.query_one("#usage", Label).update("")
            self._set_status("运行中", self._BUSY)
            self._start_spinner()
        elif etype == "llm.token":
            self._stream_buf += data.get("token", "")
            self._set_stream(self._stream_buf)
        elif etype == "llm.usage":
            self._total_in += data.get("input_tokens", 0)
            self._total_out += data.get("output_tokens", 0)
            self.query_one("#usage", Label).update(f"tokens ↑{self._total_in} ↓{self._total_out}")
        elif etype == "tool.call_started":
            self.query_one("#log", RichLog).write(f"  [dim {self._DIM}]🔧 调用工具 {data.get('tool_name', '?')} …[/]")
        elif etype == "tool.call_finished":
            mark = "✗" if data.get("is_error") else "✓"
            self.query_one("#log", RichLog).write(f"  [dim {self._DIM}]{mark} 工具 {data.get('tool_name', '?')} 完成（{data.get('duration_ms', 0)}ms）[/]")
        elif etype == "run_ended":
            self._set_status("空闲", self._DIM)
            self._stop_spinner()
            if data.get("status") == "success":
                self._write_turn("assistant", data.get("result") or "")
            else:
                self.query_one("#log", RichLog).write(f"[bold {self._ERR}][失败][/] {data.get('reason') or '未知原因'}")
            self._stream_buf = ""
            self._set_stream("")
        elif etype == "permission.requested":
            self.post_message(PermissionRequested(event["run_id"], event["data"]))

    def _set_status(self, text: str, color: str) -> None:
        """更新状态栏文字和颜色。

        输入：text（要显示的状态词）、color（16 进制颜色）
        输出：无
        功能：改 #status Label 的文字 + styles.color，一眼看出连接/运行/空闲态
        """
        label = self.query_one("#status", Label)
        label.update(text)
        label.styles.color = color

    def _set_stream(self, text: str) -> None:
        """更新流式回复面板：有内容显示正文，空则隐藏面板。

        输入：text（正在生成的助手回复，空串=隐藏面板）
        输出：无
        功能：改 #stream Label 文字并切换 display；平时隐藏不占位，只在流式输出时出现
        """
        label = self.query_one("#stream", Label)
        if text:
            label.update(escape(text))
            label.display = True
        else:
            label.display = False

    def _write_turn(self, role: str, text: str) -> None:
        """把一轮对话按角色上色写进 #log。

        谁调用：_on_submit（用户）、_on_event run_ended（助手）、_refresh_conversation（回放历史）
        输入：role（"user"/"assistant"）、text（正文）
        输出：无
        功能：先写暗色分隔线，再写彩色粗体前缀 + 转义后的正文；user 蓝、assistant 绿，
            正文里的 [ ] 不会被误当 markup
        """
        log = self.query_one("#log", RichLog)
        if role == "user":
            prefix = f"[bold {self._ACCENT}]你[/]"
        else:
            prefix = f"[bold {self._OK}]助手[/]"
        log.write(f"[dim {self._DIM}]──────────────────────────[/]")
        log.write(f"{prefix}\n{escape(text)}")

    def _start_spinner(self) -> None:
        """开始旋转指示：显示首帧并起 0.1s 定时器循环推进。

        谁调用：_on_event 的 run_started
        输入 / 输出：无
        功能：已转则不重复起；用 set_interval 驱动 _tick_spinner
        """
        self._spinner_idx = 0
        self.query_one("#spinner", Label).update(self._SPINNER[0])
        if self._spinner_timer is None:
            self._spinner_timer = self.set_interval(0.1, self._tick_spinner)

    def _stop_spinner(self) -> None:
        """停止旋转指示并清空 #spinner。

        谁调用：_on_event 的 run_ended、_on_new_session
        输入 / 输出：无
        功能：stop 定时器、置 None、清空 #spinner 文字
        """
        if self._spinner_timer is not None:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self.query_one("#spinner", Label).update("")

    def _tick_spinner(self) -> None:
        """旋转指示推进一帧（由 set_interval 定时调）。"""
        self._spinner_idx = (self._spinner_idx + 1) % len(self._SPINNER)
        self.query_one("#spinner", Label).update(self._SPINNER[self._spinner_idx])

    @on(Input.Submitted, "#goal-input")
    async def _on_submit(self, event: Input.Submitted) -> None:
        """用户回车：命中 /note 前缀走笔记，否则带上当前 session_id 发 start_run（多轮续聊）。"""
        goal = event.value.strip()
        if not goal:
            return
        self.query_one("#goal-input", Input).value = ""
        if goal.startswith("/note "):
            note = goal[len("/note "):].strip()
            if not note:
                self.query_one("#log", RichLog).write(f"[dim {self._DIM}][笔记] /note 后面要跟内容[/]")
                return
            await self._add_note(note)
            return
        self._write_turn("user", goal)
        self._set_status("运行中", self._BUSY)
        params: dict[str, Any] = {"goal": goal, "max_steps": 10}
        if self._session_id is not None:
            params["session_id"] = self._session_id
        assert self._client is not None
        resp = await self._client.request("start_run", params)
        if resp.get("ok", True):
            self._session_id = resp.get("result", {}).get("session_id", self._session_id)
            self._update_session_label()

    async def _add_note(self, note: str) -> None:
        """把一条笔记发到 daemon（session.add_note），持久到当前会话。

        谁调用：_on_submit 命中 /note 前缀时
        输入：note（笔记正文，已去前缀和空白、非空）
        输出：无（写 log 提示成功/失败）
        功能：校验 self._session_id 非 None（None 则提示先发消息建会话），
            request("session.add_note", {"session_id": ..., "note": note})，
            成功写「[笔记] 已添加」，失败写 resp 的 error
        """
        if self._session_id is None:
            self.query_one("#log", RichLog).write(f"[dim {self._DIM}][笔记] 还没有会话，先发一条消息[/]")
            return
        assert self._client is not None
        resp = await self._client.request("session.add_note", {"session_id": self._session_id, "note": note})
        if resp.get("ok", True):
            self.query_one("#log", RichLog).write(f"[dim {self._DIM}][笔记] 已添加：{escape(note)}[/]")
        else:
            self.query_one("#log", RichLog).write(f"[bold {self._ERR}][笔记] 添加失败：{resp.get('error', '未知错误')}[/]")

    @on(PermissionRequested)
    def _on_permission(self, msg: PermissionRequested) -> None:
        """弹审批弹窗：用 callback 接收用户选择，再 respond 回 daemon。"""
        modal = PermissionModal(msg.data)

        def done(allow: bool) -> None:
            """用户点完按钮后由 dismiss 同步调用，把结果发回 daemon。"""
            self.run_worker(self._respond(msg.run_id, msg.data["request_id"], allow))

        self.push_screen(modal, callback=done)

    async def _respond(self, run_id: str, request_id: str, allow: bool) -> None:
        """把批准/拒绝结果发回 daemon。"""
        assert self._client is not None
        await self._client.request("respond", {"run_id": run_id, "request_id": request_id, "allow": allow})

    @on(Button.Pressed, "#new-session")
    def _on_new_session(self) -> None:
        """点「新建会话」：清掉当前 session_id、正文、流式缓冲和 token 计数。"""
        self._session_id = None
        self._update_session_label()
        self._stream_buf = ""
        self._total_in = 0
        self._total_out = 0
        self.query_one("#log", RichLog).clear()
        self._set_stream("")
        self.query_one("#usage", Label).update("")
        self._stop_spinner()

    @on(Button.Pressed, "#switch-session")
    def _on_switch_session(self) -> None:
        """点「切换会话」：拉列表弹窗，选完回调应用。"""
        self.run_worker(self._switch_session_flow())

    async def _switch_session_flow(self) -> None:
        """拉 session.list 并弹 SessionListModal。"""
        assert self._client is not None
        resp = await self._client.request("session.list", {})
        sessions = resp.get("result", {}).get("sessions", []) if resp.get("ok", True) else []
        self.push_screen(SessionListModal(sessions), callback=self._on_session_chosen)

    @on(Button.Pressed, "#compact")
    def _on_compact(self) -> None:
        """点「压缩历史」：把当前会话历史压成摘要。

        谁调用：Button.Pressed（#compact 按钮）
        输入/输出：无
        功能：校验 self._session_id 非 None，run_worker 起 _compact_flow；
            按钮回调里不直接 await 网络请求
        """
        if self._session_id is None:
            self.query_one("#log", RichLog).write(f"[dim {self._DIM}][压缩] 还没有会话，先发一条消息[/]")
            return
        self.run_worker(self._compact_flow())

    async def _compact_flow(self) -> None:
        """发 session.compact 请求，把当前会话历史压成摘要。

        谁调用：_on_compact（run_worker 起）
        输入/输出：无
        功能：request("session.compact", {"session_id": self._session_id})，
            按 resp.ok 写「已压缩」提示，否则写 error
        """
        assert self._client is not None
        assert self._session_id is not None
        resp = await self._client.request("session.compact", {"session_id": self._session_id})
        if resp.get("ok", True):
            self.query_one("#log", RichLog).write(f"[dim {self._DIM}][压缩] 历史已压缩成摘要[/]")
        else:
            self.query_one("#log", RichLog).write(f"[bold {self._ERR}][压缩] 失败：{resp.get('error', '未知错误')}[/]")

    def _on_session_chosen(self, chosen: str | None) -> None:
        """弹窗选完的回调：用 worker 应用选择（要异步拉历史）。"""
        self.run_worker(self._apply_session_choice(chosen))

    async def _apply_session_choice(self, chosen: str | None) -> None:
        """应用会话选择：更新 session_id 并重渲染历史正文。"""
        self._session_id = chosen
        self._update_session_label()
        await self._refresh_conversation()

    async def _refresh_conversation(self) -> None:
        """从 daemon 拉当前会话历史重渲染正文（只在切会话/恢复时用，此时会话已完整）。"""
        log = self.query_one("#log", RichLog)
        if self._session_id is None:
            log.clear()
            return
        assert self._client is not None
        resp = await self._client.request("session.get", {"session_id": self._session_id})
        if not resp.get("ok", True):
            return
        log.clear()
        for m in resp.get("result", {}).get("messages", []):
            text = self._message_text(m.get("content"))
            if not text:
                continue
            self._write_turn("user" if m.get("role") == "user" else "assistant", text)

    def _message_text(self, content: Any) -> str:
        """从一条消息的 content（str 或 block 列表）抽出可读文本。"""
        if isinstance(content, str):
            return content
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    def _update_session_label(self) -> None:
        """顶部会话栏显示当前 session（短显示）。"""
        label = "新会话" if self._session_id is None else self._session_id[:8]
        self.query_one("#session-label", Label).update(f"会话: {label}")

    async def on_unmount(self) -> None:
        """应用关闭时断开 daemon 连接，避免服务端 wait_closed 挂起。"""
        if self._client is not None:
            await self._client.close()


def main() -> None:
    """salmon-tui 入口：解析 daemon 地址并运行 TUI。"""
    parser = argparse.ArgumentParser(prog="salmon-tui")
    parser.add_argument("--host", default="127.0.0.1", help="daemon 地址")
    parser.add_argument("--port", type=int, required=True, help="daemon 端口")
    args = parser.parse_args()
    SalmonTui(args.host, args.port).run()


if __name__ == "__main__":
    main()
