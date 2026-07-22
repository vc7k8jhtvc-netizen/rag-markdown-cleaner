from __future__ import annotations

import threading
from concurrent.futures import Future
from pathlib import Path

import pytest

import clean_auto.scheduler as scheduler
from clean_auto.config import (
    FilePlan,
    GracefulStop,
    ProcessOutcome,
    ProcessStats,
    RuntimeConfig,
    parse_args,
    sha256_text,
    validate_args,
)


def _config(tmp_path: Path, *, dry_run: bool = False) -> RuntimeConfig:
    return RuntimeConfig(
        api_key="key",
        base_url="https://example.test/v1",
        model="model",
        system_prompt="prompt",
        prompt_sha256="p" * 64,
        strict_validation=False,
        max_chars=1000,
        max_file_size=100_000,
        pause_file=tmp_path / "pause.flag",
        stop_file=tmp_path / "stop.flag",
        base_dir=tmp_path,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        log_dir=tmp_path / "logs",
        lock_file=tmp_path / ".lock",
        dry_run=dry_run,
    )


def _plans(tmp_path: Path, count: int) -> list[FilePlan]:
    plans: list[FilePlan] = []
    for index in range(count):
        relative = Path(f"nested/{index}.md")
        source = tmp_path / "input" / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        text = f"source-{index}\n"
        source.write_bytes(text.encode("utf-8"))
        plans.append(
            FilePlan(
                source_path=source,
                relative_path=relative,
                source_sha256=sha256_text(text),
                source_chars=len(text),
                chunks=[text],
                output_dir=tmp_path / "output" / str(index),
            )
        )
    return plans


def _success() -> ProcessOutcome:
    return ProcessOutcome(
        stats=ProcessStats(total_parts=1, success_parts=1),
        consecutive_failures=0,
    )


@pytest.mark.parametrize("value", ["1", "5"])
def test_workers_accepts_supported_range(value: str) -> None:
    args = parse_args(["--workers", value])
    validate_args(args)
    assert args.workers == int(value)


@pytest.mark.parametrize("value", ["0", "-1", "6", "one"])
def test_workers_rejects_invalid_values(value: str) -> None:
    with pytest.raises((RuntimeError, SystemExit)):
        args = parse_args(["--workers", value])
        validate_args(args)


def test_workers_default_is_serial() -> None:
    assert parse_args([]).workers == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["--workers", "2", "--dry-run"],
        ["--workers", "2", "--pause-after-files", "1"],
        ["--workers", "2", "--pause-between-files", "1"],
    ],
)
def test_workers_rejects_serial_only_options(argv: list[str]) -> None:
    args = parse_args(argv)
    with pytest.raises(RuntimeError):
        validate_args(args)


def test_bounded_scheduler_overlaps_without_submitting_all(
    tmp_path: Path,
) -> None:
    plans = _plans(tmp_path, 5)
    config = _config(tmp_path)
    started: list[str] = []
    completed: list[str] = []
    active = 0
    maximum = 0
    guard = threading.Lock()
    both_started = threading.Event()
    release = threading.Event()
    summaries: list[scheduler.SchedulerSummary] = []

    def process_file(**kwargs: object) -> ProcessOutcome:
        nonlocal active, maximum
        plan = kwargs["plan"]
        assert isinstance(plan, FilePlan)
        with guard:
            active += 1
            maximum = max(maximum, active)
            started.append(plan.relative_path.as_posix())
            if active == 2:
                both_started.set()
        release.wait(timeout=2)
        with guard:
            active -= 1
            completed.append(plan.relative_path.as_posix())
        return _success()

    results: list[scheduler.FileResult] = []
    thread = threading.Thread(
        target=lambda: summaries.append(
            scheduler.run_file_scheduler(
                plans,
                config=config,
                client=object(),
                workers=2,
                process_fn=process_file,
                on_result=results.append,
            )
        )
    )
    thread.start()
    assert both_started.wait(timeout=2)
    assert len(started) == 2
    assert len(started) < len(plans)
    assert maximum <= 2
    release.set()
    thread.join(timeout=3)
    assert not thread.is_alive()
    assert len(results) == len(plans)
    assert sorted(result.index for result in results) == list(range(len(plans)))
    assert summaries[0].submitted == len(plans)
    assert summaries[0].max_in_flight == 2


def test_scheduler_isolates_failure_and_preserves_input_result_order(
    tmp_path: Path,
) -> None:
    plans = _plans(tmp_path, 3)
    config = _config(tmp_path)
    results: list[scheduler.FileResult] = []

    def process_file(**kwargs: object) -> ProcessOutcome:
        plan = kwargs["plan"]
        assert isinstance(plan, FilePlan)
        if plan.relative_path.name == "1.md":
            raise RuntimeError("one failed")
        return _success()

    scheduler.run_file_scheduler(
        plans,
        config=config,
        client=object(),
        workers=2,
        process_fn=process_file,
        on_result=results.append,
    )

    by_index = sorted(results, key=lambda result: result.index)
    assert [result.status for result in by_index] == [
        "succeeded",
        "failed",
        "succeeded",
    ]


def test_concurrent_scheduler_has_no_cross_file_failure_breaker(
    tmp_path: Path,
) -> None:
    plans = _plans(tmp_path, 6)
    config = _config(tmp_path)
    results: list[scheduler.FileResult] = []

    def fail(**_kwargs: object) -> ProcessOutcome:
        raise RuntimeError("file failed")

    summary = scheduler.run_file_scheduler(
        plans,
        config=config,
        client=object(),
        workers=3,
        process_fn=fail,
        on_result=results.append,
    )

    assert summary.stopped is False
    assert summary.submitted == len(plans)
    assert len(results) == len(plans)
    assert all(result.status == "failed" for result in results)


def test_stop_does_not_submit_unstarted_files(
    tmp_path: Path,
) -> None:
    plans = _plans(tmp_path, 4)
    config = _config(tmp_path)
    results: list[scheduler.FileResult] = []
    submitted: list[str] = []
    gate = threading.Event()

    def process_file(**kwargs: object) -> ProcessOutcome:
        plan = kwargs["plan"]
        assert isinstance(plan, FilePlan)
        submitted.append(plan.relative_path.as_posix())
        if plan.relative_path.name == "0.md":
            config.stop_file.write_bytes(b"")
            gate.set()
            raise GracefulStop("stop")
        gate.wait(timeout=2)
        return _success()

    summary = scheduler.run_file_scheduler(
        plans,
        config=config,
        client=object(),
        workers=2,
        process_fn=process_file,
        on_result=results.append,
    )

    assert summary.stopped is True
    assert len(submitted) <= 2
    assert any(result.status == "interrupted" for result in results)


def test_source_hash_change_fails_before_process_call(
    tmp_path: Path,
) -> None:
    plans = _plans(tmp_path, 1)
    config = _config(tmp_path)
    plans[0].source_path.write_bytes(b"changed\n")
    calls = 0

    def process_file(**_kwargs: object) -> ProcessOutcome:
        nonlocal calls
        calls += 1
        return _success()

    results: list[scheduler.FileResult] = []
    scheduler.run_file_scheduler(
        plans,
        config=config,
        client=object(),
        workers=2,
        process_fn=process_file,
        on_result=results.append,
    )
    assert calls == 0
    assert results[0].status == "failed"


def test_cancelled_queued_future_returns_to_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = _plans(tmp_path, 3)
    config = _config(tmp_path)
    cancelled: list[tuple[int, FilePlan]] = []

    class ControlledExecutor:
        def __init__(self, **_kwargs: object) -> None:
            self.submitted = 0

        def __enter__(self) -> ControlledExecutor:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def submit(self, *_args: object, **_kwargs: object) -> Future:
            future: Future = Future()
            index = self.submitted
            self.submitted += 1
            if index == 0:
                future.set_result(
                    scheduler.FileResult(
                        index=0,
                        plan=plans[0],
                        status="interrupted",
                        stats=ProcessStats(total_parts=1),
                        error="stop",
                        stop_requested=True,
                    )
                )
            return future

    monkeypatch.setattr(scheduler, "ThreadPoolExecutor", ControlledExecutor)

    summary = scheduler.run_file_scheduler(
        plans,
        config=config,
        client=object(),
        workers=2,
        on_cancelled=lambda index, plan: cancelled.append((index, plan)),
    )

    assert summary.stopped is True
    assert summary.submitted == 2
    assert cancelled == [(1, plans[1])]
