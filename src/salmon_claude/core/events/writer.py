import json
from pathlib import Path
from typing import Any

from salmon_claude.core.events.bus import EventBus


class EventWriter:
    """订阅 EventBus，把属于本 run 的事件写成 events.jsonl。

    输入：bus（事件总线）/ run_id（只记录这个 run）/ output_dir（events.jsonl 所在目录）
    输出：无（副作用：往 output_dir/events.jsonl 追加 JSON 行）
    功能：构造时订阅 → 按 run_id 过滤 → 落盘 → 收到 run_ended 退订。
    """

    def __init__(self, bus: EventBus, run_id: str, output_dir: Path) -> None:
        """订阅 bus，打开 events.jsonl 准备写入。

        输入：bus（事件总线）、run_id（只写这个 run 的事件）、output_dir（目录，需已存在）
        输出：无
        功能：保存 run_id，打开 output_dir/events.jsonl（追加模式），并订阅 bus 的回调。
        """
        self._run_id = run_id
        self._closed = False
        self._file = open(output_dir / "events.jsonl", "a", encoding="utf-8")
        self._unsubscribe = bus.subscribe(self._on_event)

    async def _on_event(self, event: dict[str, Any]) -> None:
        """bus 的回调：过滤 + 落盘，收到 run_ended 时退订关文件。

        输入：event —— 带 type/run_id/timestamp/data 的事件 dict
        输出：无
        功能：① run_id 不匹配直接丢；② 匹配写一行 JSON 并 flush；③ 若是 run_ended，写完后关闭。
        """
        if self._closed or event.get("run_id") != self._run_id:
            return
        if event.get("type") == "llm.token":
            return   # token 级事件太碎，不落 events.jsonl（避免逐字 flush）；TUI 仍能从 broadcast 收到
        self._file.write(json.dumps(event, ensure_ascii=False) + "\n")
        self._file.flush()
        if event.get("type") == "run_ended":
            self.close()

    def close(self) -> None:
        """关闭文件并退订；幂等，重复调用无副作用。

        输入：无
        输出：无
        功能：退订 bus、关闭文件；用 _closed 标记，重复调用直接返回。
        """
        if self._closed:
            return
        self._closed = True
        self._unsubscribe()
        self._file.close()
