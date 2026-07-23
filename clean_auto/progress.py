from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue
from typing import Literal


ProgressKind = Literal[
    "started",
    "chunk_started",
    "chunk_completed",
    "chunk_skipped",
    "completed",
    "skipped",
    "failed",
    "interrupted",
    "retrying",
    "paused",
    "resumed",
    "quality_warning",
    "detail",
    "batch_progress",
    "notice",
]


@dataclass(frozen=True)
class ProgressContext:
    file_index: int
    total_files: int
    relative_path: Path
    part_number: int | None = None
    total_parts: int | None = None


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
    attempt: int | None = None
    max_attempts: int | None = None
    wait_seconds: float | None = None
    counts: dict[str, int] | None = None


def _file_detail(event: ProgressEvent, message: str) -> str:
    if event.part_number is not None and event.total_parts is not None:
        return f"分片 {event.part_number}/{event.total_parts}，{message}"
    return message


def _format_seconds(seconds: float | None) -> str:
    value = 0.0 if seconds is None else seconds
    return f"{value:g}"


def format_progress_event(event: ProgressEvent) -> str:
    if event.kind == "batch_progress":
        if event.counts is None:
            raise RuntimeError("批次进度事件缺少状态计数")
        counts = event.counts
        completed = sum(
            counts.get(status, 0)
            for status in ("succeeded", "skipped", "failed", "interrupted")
        )
        return (
            f"批次进度：已完成 {completed}/{counts.get('total', 0)}｜"
            f"成功 {counts.get('succeeded', 0)}｜"
            f"跳过 {counts.get('skipped', 0)}｜"
            f"失败 {counts.get('failed', 0)}｜"
            f"中断 {counts.get('interrupted', 0)}｜"
            f"处理中 {counts.get('running', 0)}｜"
            f"待处理 {counts.get('pending', 0)}"
        )

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
            f"（{_file_detail(event, '正在请求并等待模型返回')}）"
        )
    if event.kind == "chunk_completed":
        return f"{prefix}分片完成：{path}（分片 {event.part_number}/{event.total_parts}）"
    if event.kind == "chunk_skipped":
        return f"{prefix}跳过缓存：{path}（分片 {event.part_number}/{event.total_parts}）"
    if event.kind == "retrying":
        detail = _file_detail(
            event,
            f"第 {event.attempt}/{event.max_attempts} 次，"
            f"等待 {_format_seconds(event.wait_seconds)} 秒",
        )
        return f"{prefix}重试中：{path}（{detail}）"
    if event.kind == "paused":
        return f"{prefix}已暂停：{path}（{_file_detail(event, '等待 pause.flag 删除')}）"
    if event.kind == "resumed":
        return f"{prefix}继续处理：{path}（{_file_detail(event, '暂停已解除')}）"
    if event.kind == "quality_warning":
        return f"{prefix}质量提示：{path}（{_file_detail(event, event.message or '需要人工复核')}）"
    if event.kind == "detail":
        return f"{prefix}处理提示：{path}（{_file_detail(event, event.message or '')}）"
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

    def file_event(
        self,
        context: ProgressContext,
        kind: ProgressKind,
        *,
        message: str | None = None,
        error: str | None = None,
        attempt: int | None = None,
        max_attempts: int | None = None,
        wait_seconds: float | None = None,
    ) -> None:
        self.emit(
            ProgressEvent(
                file_index=context.file_index,
                total_files=context.total_files,
                relative_path=context.relative_path,
                kind=kind,
                part_number=context.part_number,
                total_parts=context.total_parts,
                message=message,
                error=error,
                attempt=attempt,
                max_attempts=max_attempts,
                wait_seconds=wait_seconds,
            )
        )

    def batch_progress(self, counts: dict[str, int]) -> None:
        self.emit(
            ProgressEvent(
                file_index=None,
                total_files=None,
                relative_path=None,
                kind="batch_progress",
                counts=dict(counts),
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
