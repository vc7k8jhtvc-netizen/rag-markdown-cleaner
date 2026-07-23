from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue
from typing import Literal


ProgressKind = Literal[
    "started",
    "chunk_started",
    "completed",
    "skipped",
    "failed",
    "interrupted",
    "notice",
]


@dataclass(frozen=True)
class ProgressEvent:
    file_index: int | None
    total_files: int | None
    relative_path: Path | None
    kind: ProgressKind
    part_number: int | None = None
    total_parts: int | None = None
    error: str | None = None
    message: str | None = None


def format_progress_event(event: ProgressEvent) -> str:
    if event.kind == "notice":
        return f"[提示] {event.message or ''}".rstrip()

    if event.file_index is None or event.total_files is None or event.relative_path is None:
        raise RuntimeError("文件进度事件缺少文件标识")

    prefix = f"[{event.file_index}/{event.total_files}] "
    path = event.relative_path.as_posix()
    if event.kind == "started":
        return f"{prefix}开始处理：{path}"
    if event.kind == "chunk_started":
        return (
            f"{prefix}处理中：{path}"
            f"（分片 {event.part_number}/{event.total_parts}）"
        )
    if event.kind == "completed":
        return f"{prefix}处理完成：{path}"
    if event.kind == "skipped":
        return f"{prefix}跳过缓存：{path}"
    if event.kind == "failed":
        return f"{prefix}处理失败：{path}（错误：{event.error or '未知错误'}）"
    if event.kind == "interrupted":
        return f"{prefix}已中断：{path}（原因：{event.error or '安全停止'}）"
    raise RuntimeError(f"未知进度事件类型：{event.kind}")


class ProgressReporter:
    """Worker-safe event producer. It never writes stdout itself."""

    def __init__(self) -> None:
        self._events: SimpleQueue[ProgressEvent] = SimpleQueue()
        self._consumer: Callable[[ProgressEvent], None] | None = None

    def set_consumer(self, consumer: Callable[[ProgressEvent], None] | None) -> None:
        self._consumer = consumer

    def emit(self, event: ProgressEvent) -> None:
        if self._consumer is not None:
            self._consumer(event)
        else:
            self._events.put(event)

    def notice(self, message: str) -> None:
        self.emit(
            ProgressEvent(
                file_index=None,
                total_files=None,
                relative_path=None,
                kind="notice",
                message=message,
            )
        )

    def drain(self) -> list[ProgressEvent]:
        events: list[ProgressEvent] = []
        while not self._events.empty():
            events.append(self._events.get())
        return events


class ProgressConsole:
    """The only progress component that writes event text to stdout."""

    def __init__(self, reporter: ProgressReporter) -> None:
        self._reporter = reporter

    def write_event(self, event: ProgressEvent) -> None:
        print(format_progress_event(event))

    def drain(self) -> None:
        for event in self._reporter.drain():
            self.write_event(event)
