from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from queue import SimpleQueue
from typing import Literal, TextIO


ProgressKind = Literal[
    "started",
    "chunk_started",
    "chunk_completed",
    "chunk_skipped",
    "chunk_failed",
    "completed",
    "skipped",
    "failed",
    "interrupted",
    "retrying",
    "paused",
    "resumed",
    "quality_warning",
    "detail",
    "stream_progress",
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
    received_bytes: int | None = None


def _file_detail(event: ProgressEvent, message: str) -> str:
    if event.part_number is not None and event.total_parts is not None:
        return f"分片 {event.part_number}/{event.total_parts}，{message}"
    return message


def _format_seconds(seconds: float | None) -> str:
    value = 0.0 if seconds is None else seconds
    return f"{value:g}"


def format_received_bytes(value: int | None) -> str:
    """Format streamed response size for the human-facing progress line."""
    size = max(0, int(value or 0))
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def format_progress_event(event: ProgressEvent) -> str:
    if event.kind == "batch_progress":
        if event.counts is None:
            raise RuntimeError("批次进度事件缺少状态计数")
        counts = event.counts
        total = counts.get("total", 0)
        completed = sum(
            counts.get(status, 0)
            for status in ("succeeded", "skipped", "failed", "interrupted")
        )
        percentage = (
            completed * 100 // total
            if total > 0
            else 100
        )
        return (
            f"批次进度：已完成 {completed}/{total}（{percentage}%）｜"
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
    if event.kind == "chunk_failed":
        return (
            f"{prefix}分片失败：{path}（"
            f"{_file_detail(event, '错误：' + (event.error or '未知错误'))}）"
        )
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
    if event.kind == "stream_progress":
        message = event.message or (
            f"已接收 {format_received_bytes(event.received_bytes)}"
        )
        return f"{prefix}接收中：{path}（{_file_detail(event, message)}）"
    if event.kind == "completed":
        percentage = event.file_index * 100 // event.total_files
        return f"{prefix}处理完成：{path}｜总体进度 {percentage}%"
    if event.kind == "skipped":
        percentage = event.file_index * 100 // event.total_files
        return f"{prefix}跳过缓存：{path}｜总体进度 {percentage}%"
    if event.kind == "failed":
        percentage = event.file_index * 100 // event.total_files
        return (
            f"{prefix}处理失败：{path}（错误：{event.error or '未知错误'}）"
            f"｜总体进度 {percentage}%"
        )
    if event.kind == "interrupted":
        percentage = event.file_index * 100 // event.total_files
        return (
            f"{prefix}已中断：{path}（原因：{event.error or '安全停止'}）"
            f"｜总体进度 {percentage}%"
        )
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
        received_bytes: int | None = None,
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
                received_bytes=received_bytes,
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
        self._live_lines: dict[tuple[int, str, int | None], str] = {}
        self._rendered_live_lines = 0
        self._virtual_terminal_enabled: bool | None = None

    @staticmethod
    def _safe_text(text: str, stream: TextIO) -> str:
        encoding = getattr(stream, "encoding", None)
        if not encoding:
            return text
        return text.encode(
            encoding,
            errors="backslashreplace",
        ).decode(encoding)

    def _supports_dashboard(self, stream: TextIO) -> bool:
        isatty = getattr(stream, "isatty", None)
        if not callable(isatty) or not isatty():
            return False
        if os.name != "nt":
            return True
        if self._virtual_terminal_enabled is not None:
            return self._virtual_terminal_enabled

        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            handle = kernel32.GetStdHandle(-11)
            mode = ctypes.c_uint()
            enabled = bool(
                kernel32.GetConsoleMode(
                    handle,
                    ctypes.byref(mode),
                )
                and kernel32.SetConsoleMode(
                    handle,
                    mode.value | 0x0004,
                )
            )
        except (AttributeError, OSError):
            enabled = False

        self._virtual_terminal_enabled = enabled
        return enabled

    @staticmethod
    def _event_key(
        event: ProgressEvent,
    ) -> tuple[int, str, int | None] | None:
        if event.file_index is None or event.relative_path is None:
            return None
        return (
            event.file_index,
            event.relative_path.as_posix(),
            event.part_number,
        )

    def _remove_finished_live_lines(
        self,
        event: ProgressEvent,
    ) -> None:
        key = self._event_key(event)
        if key is None:
            return
        file_index, path, part_number = key
        if part_number is not None:
            self._live_lines.pop(key, None)
            return
        self._live_lines = {
            live_key: text
            for live_key, text in self._live_lines.items()
            if live_key[:2] != (file_index, path)
        }

    def _redraw_dashboard(self, stream: TextIO) -> None:
        lines = list(self._live_lines.values())
        old_count = self._rendered_live_lines
        new_count = len(lines)

        if old_count > 0:
            stream.write(f"\x1b[{old_count}A")

        row_count = max(old_count, new_count)
        for index in range(row_count):
            stream.write("\r\x1b[2K")
            if index < new_count:
                stream.write(lines[index])
            stream.write("\n")

        cleared_rows = row_count - new_count
        if cleared_rows > 0:
            stream.write(f"\x1b[{cleared_rows}A")

        self._rendered_live_lines = new_count

    def _erase_dashboard(self, stream: TextIO) -> None:
        count = self._rendered_live_lines
        if count <= 0:
            return
        stream.write(f"\x1b[{count}A")
        for _ in range(count):
            stream.write("\r\x1b[2K\n")
        stream.write(f"\x1b[{count}A")
        self._rendered_live_lines = 0

    def write_event(self, event: ProgressEvent) -> None:
        stream = sys.stdout
        text = self._safe_text(
            format_progress_event(event),
            stream,
        )
        dashboard_enabled = self._supports_dashboard(
            stream
        )

        if event.kind == "stream_progress":
            if not dashboard_enabled:
                stream.write(text + "\n")
                stream.flush()
                return

            key = self._event_key(event)
            if key is not None:
                self._live_lines[key] = text
            self._redraw_dashboard(stream)
            stream.flush()
            return

        if dashboard_enabled:
            self._remove_finished_live_lines(event)
            self._erase_dashboard(stream)
        stream.write(text + "\n")
        if dashboard_enabled and self._live_lines:
            self._redraw_dashboard(stream)
        stream.flush()

    def drain(self) -> None:
        for event in self._reporter.drain():
            self.write_event(event)
