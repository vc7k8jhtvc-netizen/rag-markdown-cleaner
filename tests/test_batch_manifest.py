from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

import clean_auto.batch_manifest as batch_manifest
import clean_auto.pipeline as pipeline
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


FIXED_TIME = "2026-07-22T12:00:00"
BATCH_ID = "20260722T120000000000Z-0123456789ab"
REAL_BUILD_PLANS_SAFELY = pipeline.build_plans_safely


class FakeApiClient:
    def __init__(self, *_args: object) -> None:
        pass

    def __enter__(self) -> FakeApiClient:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


class SimulatedProcessCrash(BaseException):
    pass


def _make_plan(tmp_path: Path, relative: str) -> FilePlan:
    source_path = tmp_path / "input" / Path(relative)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_text = f"source for {relative}\n"
    source_path.write_bytes(source_text.encode("utf-8"))
    return FilePlan(
        source_path=source_path,
        relative_path=Path(relative),
        source_sha256=sha256_text(source_text),
        source_chars=len(source_text),
        chunks=[source_text],
        output_dir=tmp_path / "output" / Path(relative).parent / Path(relative).stem,
    )


def _make_config(
    tmp_path: Path,
    *,
    dry_run: bool = False,
    max_files: int = 0,
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
        max_files=max_files,
        dry_run=dry_run,
    )


def _args(
    *,
    resume_batch: str = "",
    selection_file: str = "",
    base_dir: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        yes=True,
        no_confirm=False,
        force_unlock=False,
        resume_batch=resume_batch,
        selection_file=selection_file,
        base_dir=base_dir,
    )


def _install_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    config: RuntimeConfig,
    plans: list[FilePlan],
    *,
    args: SimpleNamespace | None = None,
) -> None:
    selected_args = args or _args()
    monkeypatch.setattr(pipeline, "parse_args", lambda _argv: selected_args)
    monkeypatch.setattr(pipeline, "validate_args", lambda _args: None)
    monkeypatch.setattr(pipeline, "load_runtime_config", lambda _args: config)
    monkeypatch.setattr(
        pipeline,
        "find_input_files",
        lambda _input_dir: [plan.source_path for plan in plans],
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
    monkeypatch.setattr(pipeline, "wait_if_paused", lambda *_args: None)
    monkeypatch.setattr(pipeline, "acquire_lock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "release_lock", lambda *_args: None)
    monkeypatch.setattr(pipeline, "ApiClient", FakeApiClient)


def _success(**_kwargs: object) -> ProcessOutcome:
    return ProcessOutcome(
        stats=ProcessStats(total_parts=1, success_parts=1),
        consecutive_failures=0,
    )


def _exit_code(callable_: object) -> int:
    try:
        assert callable(callable_)
        callable_()
    except SystemExit as exc:
        return int(exc.code)
    return 0


def _latest_manifest(log_dir: Path) -> dict[str, object]:
    latest = json.loads(
        batch_manifest.latest_path(log_dir).read_bytes().decode("utf-8")
    )
    return batch_manifest.load_manifest(log_dir, latest["batch_id"])


def test_parse_resume_batch_supports_latest_and_explicit_id() -> None:
    latest = parse_args(["--resume-batch"])
    explicit = parse_args(["--resume-batch", BATCH_ID])

    assert latest.resume_batch == "latest"
    assert explicit.resume_batch == BATCH_ID


@pytest.mark.parametrize(
    "argv",
    [
        ["--resume-batch", "--selection-file", "selected.json"],
        ["--resume-batch", "--dry-run"],
    ],
)
def test_resume_batch_rejects_incompatible_options(argv: list[str]) -> None:
    with pytest.raises(RuntimeError):
        validate_args(parse_args(argv))


def test_generate_batch_id_is_valid_and_unique() -> None:
    first = batch_manifest.generate_batch_id()
    second = batch_manifest.generate_batch_id()

    assert first != second
    assert re.fullmatch(
        r"[0-9]{8}T[0-9]{12}Z-[0-9a-f]{12}",
        first,
    )


def test_create_manifest_records_schema_order_counts_and_latest(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["z.md", "nested/a.md"],
        selection_source="scan",
        selection_file=None,
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )

    assert manifest["schema"] == "rag-cleaner/batch-manifest"
    assert manifest["schema_version"] == 1
    assert manifest["batch_id"] == BATCH_ID
    assert manifest["created_at"] == FIXED_TIME
    assert manifest["updated_at"] == FIXED_TIME
    assert manifest["completed_at"] is None
    assert manifest["status"] == "running"
    assert manifest["workers"] == 1
    assert manifest["selection"] == {
        "source": "scan",
        "selection_file": None,
        "parent_batch_id": None,
    }
    assert [item["path"] for item in manifest["files"]] == [
        "z.md",
        "nested/a.md",
    ]
    assert all(item["status"] == "pending" for item in manifest["files"])
    assert manifest["counts"] == {
        "total": 2,
        "pending": 2,
        "running": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": 0,
        "interrupted": 0,
    }

    manifest_path = log_dir / "batches" / f"{BATCH_ID}.json"
    assert json.loads(manifest_path.read_bytes().decode("utf-8")) == manifest
    latest = json.loads(
        batch_manifest.latest_path(log_dir).read_bytes().decode("utf-8")
    )
    assert not batch_manifest.latest_path(log_dir).is_symlink()
    assert latest == {
        "schema": "rag-cleaner/batch-latest",
        "schema_version": 1,
        "batch_id": BATCH_ID,
        "updated_at": FIXED_TIME,
    }


def test_create_manifest_uses_atomic_json_for_manifest_and_latest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[Path, dict[str, object]]] = []
    monkeypatch.setattr(
        batch_manifest,
        "atomic_write_json",
        lambda path, data: writes.append((path, data.copy())),
    )

    batch_manifest.create_manifest(
        log_dir=tmp_path / "logs",
        relative_paths=["a.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )

    assert [path.name for path, _data in writes] == [
        f"{BATCH_ID}.json",
        "latest.json",
    ]


def test_create_manifest_never_overwrites_existing_batch(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["a.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )

    with pytest.raises(RuntimeError, match="已存在"):
        batch_manifest.create_manifest(
            log_dir=log_dir,
            relative_paths=["b.md"],
            selection_source="scan",
            batch_id=BATCH_ID,
            timestamp=FIXED_TIME,
        )


def test_manifest_schema_rejects_unknown_sensitive_fields(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["a.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    manifest["prompt_text"] = "must not persist"
    batch_manifest.manifest_path(log_dir, BATCH_ID).write_bytes(
        json.dumps(manifest).encode("utf-8")
    )

    with pytest.raises(RuntimeError, match="未知"):
        batch_manifest.load_manifest(log_dir, BATCH_ID)


@pytest.mark.parametrize(
    ("status", "expected_batch_status"),
    [
        ("succeeded", "completed"),
        ("skipped", "completed"),
        ("failed", "completed_with_failures"),
        ("pending", "incomplete"),
        ("interrupted", "incomplete"),
    ],
)
def test_file_transitions_recount_and_finalize_batch(
    tmp_path: Path,
    status: str,
    expected_batch_status: str,
) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["a.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    batch_manifest.update_file(
        log_dir,
        manifest,
        "a.md",
        status=status,
        source_sha256="a" * 64,
        error="failed safely" if status == "failed" else None,
        increment_attempts=status not in {"pending", "skipped"},
        timestamp="2026-07-22T12:01:00",
    )
    batch_manifest.finalize_manifest(
        log_dir,
        manifest,
        stopped=False,
        timestamp="2026-07-22T12:02:00",
    )

    assert manifest["status"] == expected_batch_status
    assert manifest["counts"][status] == 1
    expected_completed = (
        "2026-07-22T12:02:00"
        if expected_batch_status.startswith("completed")
        else None
    )
    assert manifest["completed_at"] == expected_completed


def test_manifest_error_is_redacted_flattened_and_limited(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["a.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    batch_manifest.update_file(
        log_dir,
        manifest,
        "a.md",
        status="failed",
        error="sk-secret-value\r\n" + "x" * 2500,
        timestamp=FIXED_TIME,
    )

    error = manifest["files"][0]["error"]
    assert isinstance(error, str)
    assert "sk-secret-value" not in error
    assert "\r" not in error
    assert "\n" not in error
    assert len(error) == 2000


def test_finalize_stopped_keeps_unfinished_files_resumable(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["running.md", "pending.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    batch_manifest.update_file(
        log_dir,
        manifest,
        "running.md",
        status="interrupted",
        increment_attempts=True,
        timestamp=FIXED_TIME,
    )
    batch_manifest.finalize_manifest(
        log_dir,
        manifest,
        stopped=True,
        timestamp=FIXED_TIME,
    )

    assert manifest["status"] == "stopped"
    assert manifest["completed_at"] is None
    assert manifest["counts"]["interrupted"] == 1
    assert manifest["counts"]["pending"] == 1


def test_prepare_resume_converts_running_and_updates_latest(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["a.md", "b.md"],
        selection_source="selection_file",
        selection_file="selected.json",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    batch_manifest.update_file(
        log_dir,
        manifest,
        "a.md",
        status="running",
        increment_attempts=True,
        timestamp=FIXED_TIME,
    )

    batch_manifest.prepare_resume(
        log_dir,
        manifest,
        timestamp="2026-07-22T13:00:00",
    )

    assert manifest["files"][0]["status"] == "interrupted"
    assert manifest["files"][1]["status"] == "pending"
    assert manifest["selection"]["source"] == "resume"
    assert manifest["status"] == "running"
    latest = json.loads(
        batch_manifest.latest_path(log_dir).read_bytes().decode("utf-8")
    )
    assert latest["batch_id"] == BATCH_ID
    assert latest["updated_at"] == "2026-07-22T13:00:00"


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"schema": "unknown", "schema_version": 1}, "schema"),
        (
            {"schema": "rag-cleaner/batch-manifest", "schema_version": 2},
            "schema_version",
        ),
    ],
)
def test_load_manifest_rejects_unknown_schema(
    tmp_path: Path,
    payload: dict[str, object],
    message: str,
) -> None:
    path = tmp_path / "logs" / "batches" / f"{BATCH_ID}.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(json.dumps(payload).encode("utf-8"))

    with pytest.raises(RuntimeError, match=message):
        batch_manifest.load_manifest(tmp_path / "logs", BATCH_ID)


def test_load_manifest_rejects_corrupt_json_and_invalid_id(tmp_path: Path) -> None:
    path = tmp_path / "logs" / "batches" / f"{BATCH_ID}.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not-json")

    with pytest.raises(RuntimeError, match="JSON"):
        batch_manifest.load_manifest(tmp_path / "logs", BATCH_ID)
    with pytest.raises(RuntimeError, match="batch ID"):
        batch_manifest.load_manifest(tmp_path / "logs", "../outside")


def test_load_manifest_rejects_counts_that_do_not_match_files(
    tmp_path: Path,
) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["a.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    manifest["counts"]["pending"] = 0
    batch_manifest.manifest_path(log_dir, BATCH_ID).write_bytes(
        json.dumps(manifest).encode("utf-8")
    )

    with pytest.raises(RuntimeError, match="counts"):
        batch_manifest.load_manifest(log_dir, BATCH_ID)


def test_load_latest_rejects_missing_manifest(tmp_path: Path) -> None:
    latest = batch_manifest.latest_path(tmp_path / "logs")
    latest.parent.mkdir(parents=True)
    latest.write_bytes(
        json.dumps(
            {
                "schema": "rag-cleaner/batch-latest",
                "schema_version": 1,
                "batch_id": BATCH_ID,
                "updated_at": FIXED_TIME,
            }
        ).encode("utf-8")
    )

    with pytest.raises(RuntimeError, match="不存在"):
        batch_manifest.load_resume_manifest(tmp_path / "logs", "latest")


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        (b"not-json", "JSON"),
        (
            json.dumps(
                {
                    "schema": "unknown",
                    "schema_version": 1,
                }
            ).encode("utf-8"),
            "schema",
        ),
        (
            json.dumps(
                {
                    "schema": "rag-cleaner/batch-latest",
                    "schema_version": 2,
                }
            ).encode("utf-8"),
            "schema_version",
        ),
    ],
)
def test_load_latest_rejects_corrupt_or_unknown_pointer(
    tmp_path: Path,
    raw: bytes,
    message: str,
) -> None:
    latest = batch_manifest.latest_path(tmp_path / "logs")
    latest.parent.mkdir(parents=True)
    latest.write_bytes(raw)

    with pytest.raises(RuntimeError, match=message):
        batch_manifest.load_resume_manifest(tmp_path / "logs", "latest")


def test_manifest_contains_no_prompt_source_or_sensitive_configuration(
    tmp_path: Path,
) -> None:
    manifest = batch_manifest.create_manifest(
        log_dir=tmp_path / "logs",
        relative_paths=["a.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    serialized = json.dumps(manifest, ensure_ascii=False)

    assert "prompt" not in serialized.lower()
    assert "api_key" not in serialized.lower()
    assert "source for" not in serialized
    assert "test-key" not in serialized


def test_non_dry_scan_creates_completed_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = [_make_plan(tmp_path, "b.md"), _make_plan(tmp_path, "nested/a.md")]
    config = _make_config(tmp_path)
    _install_pipeline(monkeypatch, config, plans)
    calls: list[str] = []

    def process(plan: FilePlan, **_kwargs: object) -> ProcessOutcome:
        current = _latest_manifest(config.log_dir)
        item = next(entry for entry in current["files"] if entry["path"] == plan.relative_path.as_posix())
        assert item["status"] == "running"
        calls.append(plan.relative_path.as_posix())
        return _success()

    monkeypatch.setattr(pipeline, "process_file", process)

    assert _exit_code(lambda: pipeline.main([])) == 0
    manifest = _latest_manifest(config.log_dir)
    assert calls == ["b.md", "nested/a.md"]
    assert [item["path"] for item in manifest["files"]] == calls
    assert all(item["status"] == "succeeded" for item in manifest["files"])
    assert all(item["source_sha256"] for item in manifest["files"])
    assert manifest["status"] == "completed"
    assert manifest["counts"]["succeeded"] == 2


def test_non_dry_selection_file_records_selection_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _make_plan(tmp_path, "nested/first.md")
    second = _make_plan(tmp_path, "second.md")
    selection = tmp_path / "configs" / "selected.json"
    selection.parent.mkdir(parents=True)
    selection.write_bytes(
        json.dumps(
            {
                "schema": "rag-cleaner/selection",
                "schema_version": 1,
                "source": {"kind": "files", "root": None},
                "paths": ["nested/first.md", "second.md"],
            }
        ).encode("utf-8")
    )
    config = _make_config(tmp_path)
    args = _args(selection_file="configs/selected.json", base_dir=str(tmp_path))
    _install_pipeline(monkeypatch, config, [first, second], args=args)
    observed_sources: list[Path] = []

    def build(**kwargs: object) -> tuple[list[FilePlan], list[dict[str, str]]]:
        observed_sources.extend(kwargs["source_paths"])
        return [first, second], []

    monkeypatch.setattr(pipeline, "build_plans_safely", build)
    monkeypatch.setattr(pipeline, "process_file", _success)

    assert _exit_code(lambda: pipeline.main([])) == 0
    manifest = _latest_manifest(config.log_dir)
    assert observed_sources == [first.source_path.resolve(), second.source_path.resolve()]
    assert manifest["selection"] == {
        "source": "selection_file",
        "selection_file": "configs/selected.json",
        "parent_batch_id": None,
    }


def test_current_final_cache_is_recorded_as_skipped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, "cached.md")
    config = _make_config(tmp_path)
    _install_pipeline(monkeypatch, config, [plan])
    monkeypatch.setattr(pipeline, "plan_has_pending_chunks", lambda **_kwargs: False)
    monkeypatch.setattr(pipeline, "final_output_is_current", lambda **_kwargs: True)
    monkeypatch.setattr(
        pipeline,
        "process_file",
        lambda **_kwargs: pytest.fail("cached final must not be processed"),
    )

    assert _exit_code(lambda: pipeline.main([])) == 0
    manifest = _latest_manifest(config.log_dir)
    assert manifest["files"][0]["status"] == "skipped"
    assert manifest["status"] == "completed"


def test_file_failure_isolated_and_manifest_completed_with_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _make_plan(tmp_path, "first.md")
    second = _make_plan(tmp_path, "second.md")
    config = _make_config(tmp_path)
    _install_pipeline(monkeypatch, config, [first, second])
    calls: list[str] = []

    def process(plan: FilePlan, **_kwargs: object) -> ProcessOutcome:
        calls.append(plan.relative_path.as_posix())
        if plan is first:
            raise RuntimeError("file failed with sk-secret-value")
        return _success()

    monkeypatch.setattr(pipeline, "process_file", process)

    assert _exit_code(lambda: pipeline.main([])) == 2
    manifest = _latest_manifest(config.log_dir)
    assert calls == ["first.md", "second.md"]
    assert [item["status"] for item in manifest["files"]] == [
        "failed",
        "succeeded",
    ]
    assert manifest["status"] == "completed_with_failures"
    assert manifest["counts"]["failed"] == 1
    assert "sk-secret-value" not in json.dumps(manifest)


def test_planning_failure_is_recorded_and_valid_file_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    valid = _make_plan(tmp_path, "valid.md")
    missing = tmp_path / "input" / "missing.md"
    config = _make_config(tmp_path)
    _install_pipeline(monkeypatch, config, [valid])
    monkeypatch.setattr(
        pipeline,
        "find_input_files",
        lambda _input_dir: [missing, valid.source_path],
    )
    monkeypatch.setattr(
        pipeline,
        "build_plans_safely",
        REAL_BUILD_PLANS_SAFELY,
    )
    monkeypatch.setattr(pipeline, "process_file", _success)

    assert _exit_code(lambda: pipeline.main([])) == 2
    manifest = _latest_manifest(config.log_dir)
    assert [item["status"] for item in manifest["files"]] == [
        "failed",
        "succeeded",
    ]
    assert manifest["files"][0]["source_sha256"] is None
    assert manifest["files"][0]["error"]
    assert manifest["status"] == "completed_with_failures"


def test_graceful_stop_interrupts_current_and_keeps_later_pending(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _make_plan(tmp_path, "first.md")
    second = _make_plan(tmp_path, "second.md")
    config = _make_config(tmp_path)
    _install_pipeline(monkeypatch, config, [first, second])
    monkeypatch.setattr(
        pipeline,
        "process_file",
        lambda **_kwargs: (_ for _ in ()).throw(GracefulStop("stop now")),
    )

    assert _exit_code(lambda: pipeline.main([])) == 1
    manifest = _latest_manifest(config.log_dir)
    assert [item["status"] for item in manifest["files"]] == [
        "interrupted",
        "pending",
    ]
    assert manifest["status"] == "stopped"


def test_process_crash_leaves_running_for_next_resume(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, "crashed.md")
    config = _make_config(tmp_path)
    _install_pipeline(monkeypatch, config, [plan])
    monkeypatch.setattr(
        pipeline,
        "process_file",
        lambda **_kwargs: (_ for _ in ()).throw(
            SimulatedProcessCrash()
        ),
    )

    with pytest.raises(SimulatedProcessCrash):
        pipeline.main([])

    crashed = _latest_manifest(config.log_dir)
    assert crashed["files"][0]["status"] == "running"
    assert crashed["status"] == "running"

    batch_manifest.prepare_resume(
        config.log_dir,
        crashed,
        timestamp=FIXED_TIME,
    )
    assert crashed["files"][0]["status"] == "interrupted"


def test_max_files_leaves_unselected_items_pending_and_batch_incomplete(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = [
        _make_plan(tmp_path, "first.md"),
        _make_plan(tmp_path, "second.md"),
        _make_plan(tmp_path, "third.md"),
    ]
    config = _make_config(tmp_path, max_files=2)
    _install_pipeline(monkeypatch, config, plans)
    calls: list[str] = []

    def process(plan: FilePlan, **_kwargs: object) -> ProcessOutcome:
        calls.append(plan.relative_path.as_posix())
        return _success()

    monkeypatch.setattr(pipeline, "process_file", process)

    assert _exit_code(lambda: pipeline.main([])) == 0
    manifest = _latest_manifest(config.log_dir)
    assert calls == ["first.md", "second.md"]
    assert [item["status"] for item in manifest["files"]] == [
        "succeeded",
        "succeeded",
        "pending",
    ]
    assert manifest["status"] == "incomplete"


def test_resume_latest_only_runs_pending_and_interrupted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = [
        _make_plan(tmp_path, "succeeded.md"),
        _make_plan(tmp_path, "failed.md"),
        _make_plan(tmp_path, "running.md"),
        _make_plan(tmp_path, "pending.md"),
        _make_plan(tmp_path, "skipped.md"),
    ]
    config = _make_config(tmp_path)
    manifest = batch_manifest.create_manifest(
        log_dir=config.log_dir,
        relative_paths=[plan.relative_path.as_posix() for plan in plans],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    for relative, status in [
        ("succeeded.md", "succeeded"),
        ("failed.md", "failed"),
        ("running.md", "running"),
        ("skipped.md", "skipped"),
    ]:
        batch_manifest.update_file(
            config.log_dir,
            manifest,
            relative,
            status=status,
            source_sha256="0" * 64,
            error="old failure" if status == "failed" else None,
            timestamp=FIXED_TIME,
        )
    args = _args(resume_batch="latest")
    eligible = [plans[2], plans[3]]
    _install_pipeline(monkeypatch, config, eligible, args=args)
    calls: list[str] = []

    def build(**kwargs: object) -> tuple[list[FilePlan], list[dict[str, str]]]:
        assert kwargs["source_paths"] == [
            plans[2].source_path.resolve(),
            plans[3].source_path.resolve(),
        ]
        return eligible, []

    def process(plan: FilePlan, **_kwargs: object) -> ProcessOutcome:
        calls.append(plan.relative_path.as_posix())
        return _success()

    monkeypatch.setattr(pipeline, "build_plans_safely", build)
    monkeypatch.setattr(pipeline, "process_file", process)

    assert _exit_code(lambda: pipeline.main([])) == 2
    resumed = batch_manifest.load_manifest(config.log_dir, BATCH_ID)
    assert calls == ["running.md", "pending.md"]
    assert [item["status"] for item in resumed["files"]] == [
        "succeeded",
        "failed",
        "succeeded",
        "succeeded",
        "skipped",
    ]
    assert resumed["files"][2]["attempts"] == 1
    assert resumed["files"][2]["source_sha256"] == plans[2].source_sha256
    assert resumed["selection"]["source"] == "resume"
    assert resumed["status"] == "completed_with_failures"


def test_explicit_resume_updates_latest_to_historical_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_plan = _make_plan(tmp_path, "old.md")
    config = _make_config(tmp_path)
    batch_manifest.create_manifest(
        log_dir=config.log_dir,
        relative_paths=["old.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    newer_id = "20260722T130000000000Z-fedcba987654"
    batch_manifest.create_manifest(
        log_dir=config.log_dir,
        relative_paths=["new.md"],
        selection_source="scan",
        batch_id=newer_id,
        timestamp="2026-07-22T13:00:00",
    )
    _install_pipeline(
        monkeypatch,
        config,
        [old_plan],
        args=_args(resume_batch=BATCH_ID),
    )
    monkeypatch.setattr(
        pipeline,
        "build_plans_safely",
        REAL_BUILD_PLANS_SAFELY,
    )
    monkeypatch.setattr(pipeline, "process_file", _success)

    assert _exit_code(lambda: pipeline.main([])) == 0
    latest = json.loads(
        batch_manifest.latest_path(config.log_dir).read_bytes().decode("utf-8")
    )
    assert latest["batch_id"] == BATCH_ID


def test_resume_replans_current_source_and_updates_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, "changed.md")
    config = _make_config(tmp_path)
    manifest = batch_manifest.create_manifest(
        log_dir=config.log_dir,
        relative_paths=["changed.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    batch_manifest.update_file(
        config.log_dir,
        manifest,
        "changed.md",
        status="interrupted",
        source_sha256="0" * 64,
        timestamp=FIXED_TIME,
    )
    _install_pipeline(
        monkeypatch,
        config,
        [plan],
        args=_args(resume_batch=BATCH_ID),
    )
    monkeypatch.setattr(
        pipeline,
        "build_plans_safely",
        REAL_BUILD_PLANS_SAFELY,
    )
    monkeypatch.setattr(pipeline, "process_file", _success)

    assert _exit_code(lambda: pipeline.main([])) == 0
    resumed = batch_manifest.load_manifest(config.log_dir, BATCH_ID)
    assert resumed["files"][0]["source_sha256"] == plan.source_sha256
    assert resumed["files"][0]["source_sha256"] != "0" * 64


def test_dry_run_creates_no_manifest_or_latest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, "dry.md")
    config = _make_config(tmp_path, dry_run=True)
    _install_pipeline(monkeypatch, config, [plan])
    monkeypatch.setattr(pipeline, "process_file", _success)

    assert _exit_code(lambda: pipeline.main([])) == 0
    assert not (config.log_dir / "batches").exists()


def test_dry_run_does_not_update_existing_latest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, "dry.md")
    config = _make_config(tmp_path, dry_run=True)
    batch_manifest.create_manifest(
        log_dir=config.log_dir,
        relative_paths=["previous.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp=FIXED_TIME,
    )
    latest = batch_manifest.latest_path(config.log_dir)
    original_latest = latest.read_bytes()
    _install_pipeline(monkeypatch, config, [plan])
    monkeypatch.setattr(pipeline, "process_file", _success)

    assert _exit_code(lambda: pipeline.main([])) == 0
    assert latest.read_bytes() == original_latest
