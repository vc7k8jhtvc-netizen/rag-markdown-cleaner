from __future__ import annotations

import threading
from pathlib import Path

from clean_auto.progress import (
    ProgressConsole,
    ProgressEvent,
    ProgressReporter,
    format_progress_event,
)
import clean_auto.control as control


ROOT = Path(__file__).resolve().parents[1]


def _event(
    kind: str,
    *,
    part_number: int | None = None,
    total_parts: int | None = None,
    error: str | None = None,
) -> ProgressEvent:
    return ProgressEvent(
        file_index=1,
        total_files=3,
        relative_path=Path("法规/安全生产法.md"),
        kind=kind,
        part_number=part_number,
        total_parts=total_parts,
        error=error,
    )


def test_progress_event_formats_complete_chinese_file_lines() -> None:
    assert format_progress_event(_event("started")) == (
        "[1/3] 开始处理：法规/安全生产法.md"
    )
    assert format_progress_event(
        _event("chunk_started", part_number=3, total_parts=12)
    ) == "[1/3] 处理中：法规/安全生产法.md（分片 3/12）"
    assert format_progress_event(_event("skipped")) == (
        "[1/3] 跳过缓存：法规/安全生产法.md"
    )
    assert format_progress_event(_event("completed")) == (
        "[1/3] 处理完成：法规/安全生产法.md"
    )
    assert format_progress_event(_event("failed", error="接口超时")) == (
        "[1/3] 处理失败：法规/安全生产法.md（错误：接口超时）"
    )
    assert format_progress_event(_event("interrupted", error="检测到停止文件")) == (
        "[1/3] 已中断：法规/安全生产法.md（原因：检测到停止文件）"
    )


def test_workers_queue_events_and_only_main_console_writes_stdout(
    capsys,
) -> None:
    reporter = ProgressReporter()
    console = ProgressConsole(reporter)
    ready = threading.Barrier(3)

    def worker(index: int) -> None:
        ready.wait()
        reporter.emit(
            ProgressEvent(
                file_index=index,
                total_files=2,
                relative_path=Path(f"资料/{index}.md"),
                kind="completed",
            )
        )

    first = threading.Thread(target=worker, args=(1,))
    second = threading.Thread(target=worker, args=(2,))
    first.start()
    second.start()
    ready.wait()
    first.join(timeout=1)
    second.join(timeout=1)

    assert not first.is_alive()
    assert not second.is_alive()
    assert capsys.readouterr().out == ""

    console.drain()
    lines = capsys.readouterr().out.splitlines()
    assert sorted(lines) == [
        "[1/2] 处理完成：资料/1.md",
        "[2/2] 处理完成：资料/2.md",
    ]
    assert all("\r" not in line for line in lines)


def test_worker_path_modules_do_not_write_stdout_directly() -> None:
    for relative_path in (
        "clean_auto/processor.py",
        "clean_auto/api_client.py",
        "clean_auto/assembly.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "print(" not in source, relative_path


def test_control_reports_pause_through_the_worker_queue(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    pause_file = tmp_path / "pause.flag"
    stop_file = tmp_path / "stop.flag"
    pause_file.write_text("pause", encoding="utf-8")
    reporter = ProgressReporter()
    monkeypatch.setattr(control.time, "sleep", lambda _seconds: pause_file.unlink())

    control.wait_if_paused(
        pause_file=pause_file,
        stop_file=stop_file,
        reporter=reporter,
    )

    assert capsys.readouterr().out == ""
    assert [event.kind for event in reporter.drain()] == ["notice", "notice"]
