from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import clean_auto.pipeline as pipeline
from clean_auto.config import (
    FilePlan,
    ProcessOutcome,
    ProcessStats,
    RuntimeConfig,
    sha256_text,
)


class FakeApiClient:
    def __init__(
        self,
        *_args: object,
    ) -> None:
        self.client = object()

    def __enter__(self) -> object:
        return self.client

    def __exit__(
        self,
        *_args: object,
    ) -> None:
        return None


def _make_plan(
    tmp_path: Path,
    name: str = "sample",
) -> FilePlan:
    source_path = tmp_path / "input" / f"{name}.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_text = f"source for {name}"
    source_path.write_text(source_text, encoding="utf-8")

    return FilePlan(
        source_path=source_path,
        relative_path=Path(f"{name}.md"),
        source_sha256=sha256_text(source_text),
        source_chars=len(source_text),
        chunks=[source_text],
        output_dir=tmp_path / "output" / name,
    )


def _make_config(
    tmp_path: Path,
    dry_run: bool = False,
) -> RuntimeConfig:
    return RuntimeConfig(
        api_key="test-key",
        base_url="https://example.com/v1",
        model="test-model",
        system_prompt="test prompt",
        prompt_sha256="prompt-hash",
        strict_validation=False,
        max_chars=1000,
        max_file_size=100_000,
        pause_file=tmp_path / "pause.flag",
        stop_file=tmp_path / "stop.flag",
        base_dir=tmp_path,
        input_dir=tmp_path / "input",
        output_dir=tmp_path / "output",
        log_dir=tmp_path / "logs",
        lock_file=tmp_path / "run.lock",
        dry_run=dry_run,
    )


def _install_pipeline_inputs(
    monkeypatch: pytest.MonkeyPatch,
    config: RuntimeConfig,
    plans: list[FilePlan],
) -> None:
    args = SimpleNamespace(
        yes=True,
        no_confirm=False,
        force_unlock=False,
    )
    monkeypatch.setattr(
        pipeline,
        "parse_args",
        lambda _argv: args,
    )
    monkeypatch.setattr(
        pipeline,
        "validate_args",
        lambda _args: None,
    )
    monkeypatch.setattr(
        pipeline,
        "load_runtime_config",
        lambda _args: config,
    )
    monkeypatch.setattr(
        pipeline,
        "find_input_files",
        lambda _input_dir: [
            plan.source_path
            for plan in plans
        ],
    )
    monkeypatch.setattr(
        pipeline,
        "build_plans_safely",
        lambda **_kwargs: (plans, []),
    )
    monkeypatch.setattr(
        pipeline,
        "plan_has_pending_chunks",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(
        pipeline,
        "wait_if_paused",
        lambda *_args, **_kwargs: None,
    )


def _exit_code(
    callable_: Callable[[], object],
) -> int:
    try:
        callable_()
    except SystemExit as exc:
        return int(exc.code)
    return 0


def test_dry_run_avoids_lock_and_api_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover dry-run planning without lock acquisition or API construction."""
    plan = _make_plan(tmp_path)
    config = _make_config(tmp_path, dry_run=True)
    _install_pipeline_inputs(monkeypatch, config, [plan])
    process_calls: list[dict[str, object]] = []

    def fake_process_file(
        **kwargs: object,
    ) -> ProcessOutcome:
        process_calls.append(kwargs)
        return ProcessOutcome(
            stats=ProcessStats(
                total_parts=1,
                success_parts=1,
            ),
            consecutive_failures=0,
        )

    monkeypatch.setattr(
        pipeline,
        "process_file",
        fake_process_file,
    )
    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: pytest.fail(
            "dry-run must not acquire a lock"
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "release_lock",
        lambda *_args, **_kwargs: pytest.fail(
            "dry-run must not release an unowned lock"
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "ApiClient",
        lambda *_args, **_kwargs: pytest.fail(
            "dry-run must not construct ApiClient"
        ),
    )

    assert _exit_code(lambda: pipeline.main([])) == 0
    assert len(process_calls) == 1
    assert process_calls[0]["client"] is None


def test_lock_is_released_when_api_client_enter_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover lock release for an exception after acquisition but before processing."""
    plan = _make_plan(tmp_path)
    config = _make_config(tmp_path)
    _install_pipeline_inputs(monkeypatch, config, [plan])
    lock_events: list[str] = []

    class FailingApiClient:
        def __init__(self, *_args: object) -> None:
            pass

        def __enter__(self) -> object:
            raise RuntimeError("client setup failed")

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: lock_events.append("acquire"),
    )
    monkeypatch.setattr(
        pipeline,
        "release_lock",
        lambda *_args, **_kwargs: lock_events.append("release"),
    )
    monkeypatch.setattr(
        pipeline,
        "ApiClient",
        FailingApiClient,
    )

    with pytest.raises(RuntimeError, match="client setup failed"):
        pipeline.main([])

    assert lock_events == ["acquire", "release"]


@pytest.mark.parametrize(
    ("result_kind", "expected_code"),
    [
        ("success", 0),
        ("stopped", 1),
        ("failed", 2),
    ],
)
def test_main_exit_codes_and_lock_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result_kind: str,
    expected_code: int,
) -> None:
    """Cover CLI exit semantics and lock cleanup for normal outcomes."""
    plan = _make_plan(tmp_path)
    config = _make_config(tmp_path)
    _install_pipeline_inputs(monkeypatch, config, [plan])
    lock_events: list[str] = []

    if result_kind == "success":
        outcome = ProcessOutcome(
            stats=ProcessStats(
                total_parts=1,
                success_parts=1,
            ),
            consecutive_failures=0,
        )
    elif result_kind == "stopped":
        outcome = ProcessOutcome(
            stats=ProcessStats(total_parts=1),
            consecutive_failures=0,
            stopped=True,
        )
    else:
        outcome = ProcessOutcome(
            stats=ProcessStats(
                total_parts=1,
                failed_parts=1,
            ),
            consecutive_failures=1,
        )

    monkeypatch.setattr(
        pipeline,
        "process_file",
        lambda **_kwargs: outcome,
    )
    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: lock_events.append("acquire"),
    )
    monkeypatch.setattr(
        pipeline,
        "release_lock",
        lambda *_args, **_kwargs: lock_events.append("release"),
    )
    monkeypatch.setattr(
        pipeline,
        "ApiClient",
        FakeApiClient,
    )

    assert _exit_code(lambda: pipeline.main([])) == expected_code
    assert lock_events == ["acquire", "release"]


def test_one_file_exception_does_not_block_later_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover file-level failure isolation and propagation of failure counters."""
    first_plan = _make_plan(tmp_path, "first")
    second_plan = _make_plan(tmp_path, "second")
    config = _make_config(tmp_path)
    _install_pipeline_inputs(
        monkeypatch,
        config,
        [first_plan, second_plan],
    )
    process_calls: list[tuple[str, int]] = []
    lock_events: list[str] = []

    def fake_process_file(
        plan: FilePlan,
        initial_consecutive_failures: int,
        **_kwargs: object,
    ) -> ProcessOutcome:
        process_calls.append(
            (
                plan.relative_path.as_posix(),
                initial_consecutive_failures,
            )
        )
        if plan is first_plan:
            raise RuntimeError("first file failed")

        return ProcessOutcome(
            stats=ProcessStats(
                total_parts=1,
                success_parts=1,
            ),
            consecutive_failures=0,
        )

    monkeypatch.setattr(
        pipeline,
        "process_file",
        fake_process_file,
    )
    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: lock_events.append("acquire"),
    )
    monkeypatch.setattr(
        pipeline,
        "release_lock",
        lambda *_args, **_kwargs: lock_events.append("release"),
    )
    monkeypatch.setattr(
        pipeline,
        "ApiClient",
        FakeApiClient,
    )

    assert _exit_code(lambda: pipeline.main([])) == 2
    assert process_calls == [
        ("first.md", 0),
        ("second.md", 1),
    ]
    assert lock_events == ["acquire", "release"]
