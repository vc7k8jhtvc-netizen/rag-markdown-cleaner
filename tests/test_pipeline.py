from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest

import clean_auto.assembly as assembly
import clean_auto.pipeline as pipeline
import clean_auto.processor as processor
from clean_auto.api_client import build_user_message
from clean_auto.chunking import (
    build_file_plan,
    get_chunk_paths,
)
from clean_auto.config import (
    FilePlan,
    ProcessOutcome,
    ProcessStats,
    RequestResult,
    RuntimeConfig,
    read_text,
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


class RecordingApiClient:
    def __init__(
        self,
        responses: list[str | Exception],
    ) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def __enter__(self) -> RecordingApiClient:
        return self

    def __exit__(
        self,
        *_args: object,
    ) -> None:
        return None

    def stream_request(
        self,
        **kwargs: object,
    ) -> RequestResult:
        self.calls.append(kwargs)

        if not self.responses:
            raise AssertionError(
                "API mock received an unexpected request"
            )

        response = self.responses.pop(0)

        if isinstance(response, Exception):
            raise response

        return RequestResult(
            text=response,
            elapsed_seconds=0.1,
            received_events=1,
            received_chars=len(response),
        )


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


def _quality_report() -> SimpleNamespace:
    return SimpleNamespace(
        severe_errors=[],
        review_required=False,
        warnings=[],
        retained_ratio=1.0,
        removed_ratio=0.0,
        to_dict=lambda: {
            "severe_errors": [],
            "warnings": [],
            "review_required": False,
        },
    )


def _install_real_pipeline_flow(
    monkeypatch: pytest.MonkeyPatch,
    config: RuntimeConfig,
    client: RecordingApiClient,
    assembly_sources: list[str],
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
        "ApiClient",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "release_lock",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "wait_if_paused",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        processor,
        "wait_if_paused",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        processor,
        "validate_result",
        lambda **_kwargs: ([], []),
    )
    monkeypatch.setattr(
        processor,
        "assess_quality",
        lambda **_kwargs: _quality_report(),
    )

    def capture_assembly_quality(
        input_text: str,
        output_text: str,
    ) -> SimpleNamespace:
        del output_text
        assembly_sources.append(input_text)
        return _quality_report()

    monkeypatch.setattr(
        assembly,
        "assess_quality",
        capture_assembly_quality,
    )
    monkeypatch.setattr(
        assembly,
        "sync_review_copy",
        lambda **_kwargs: None,
    )


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


def test_serial_progress_events_and_final_summary_use_manifest_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = _make_plan(tmp_path)
    config = _make_config(tmp_path)
    _install_pipeline_inputs(monkeypatch, config, [plan])

    def fake_process_file(**kwargs: object) -> ProcessOutcome:
        reporter = kwargs["reporter"]
        assert isinstance(reporter, object)
        reporter.emit(
            pipeline.ProgressEvent(
                file_index=1,
                total_files=1,
                relative_path=plan.relative_path,
                kind="chunk_started",
                part_number=1,
                total_parts=1,
            )
        )
        return ProcessOutcome(
            stats=ProcessStats(total_parts=1, success_parts=1),
            consecutive_failures=0,
        )

    monkeypatch.setattr(pipeline, "process_file", fake_process_file)
    monkeypatch.setattr(pipeline, "ApiClient", FakeApiClient)
    monkeypatch.setattr(pipeline, "acquire_lock", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(pipeline, "release_lock", lambda *_args, **_kwargs: None)

    assert _exit_code(lambda: pipeline.main([])) == 0

    output = capsys.readouterr().out
    assert "[1/1] 开始处理：sample.md" in output
    assert "[1/1] 处理中：sample.md（分片 1/1）" in output
    assert "[1/1] 处理完成：sample.md" in output
    assert "批次完成：总数 1｜成功 1｜跳过 0｜失败 0｜中断 0｜待处理 0" in output


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


def test_workers_one_preserves_order_and_file_pause_behavior(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = [_make_plan(tmp_path, "first"), _make_plan(tmp_path, "second")]
    config = _make_config(tmp_path)
    config.workers = 1
    config.pause_after_files = 1
    config.pause_between_files = 0.25
    _install_pipeline_inputs(monkeypatch, config, plans)
    events: list[str] = []

    def process(plan: FilePlan, **_kwargs: object) -> ProcessOutcome:
        events.append(f"process:{plan.relative_path.as_posix()}")
        return ProcessOutcome(
            stats=ProcessStats(total_parts=1, success_parts=1),
            consecutive_failures=0,
        )

    monkeypatch.setattr(pipeline, "process_file", process)
    monkeypatch.setattr(pipeline, "ApiClient", FakeApiClient)
    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "release_lock",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "wait_for_enter_or_stop",
        lambda *_args: events.append("pause-after"),
    )
    monkeypatch.setattr(
        pipeline,
        "controlled_sleep",
        lambda seconds, *_args: events.append(f"sleep:{seconds}"),
    )

    assert _exit_code(lambda: pipeline.main([])) == 0
    assert events == [
        "process:first.md",
        "pause-after",
        "sleep:0.25",
        "process:second.md",
    ]


def test_workers_one_keeps_five_file_failure_breaker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plans = [_make_plan(tmp_path, f"file-{index}") for index in range(6)]
    config = _make_config(tmp_path)
    config.workers = 1
    _install_pipeline_inputs(monkeypatch, config, plans)
    calls: list[str] = []

    def fail(plan: FilePlan, **_kwargs: object) -> ProcessOutcome:
        calls.append(plan.relative_path.as_posix())
        raise RuntimeError("file failed")

    monkeypatch.setattr(pipeline, "process_file", fail)
    monkeypatch.setattr(pipeline, "ApiClient", FakeApiClient)
    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pipeline,
        "release_lock",
        lambda *_args, **_kwargs: None,
    )

    assert _exit_code(lambda: pipeline.main([])) == 1
    assert calls == [f"file-{index}.md" for index in range(5)]


def test_planning_isolates_an_undecodable_file(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    log_dir = tmp_path / "logs"
    valid_path = input_dir / "valid.md"
    invalid_path = input_dir / "invalid.md"
    valid_text = "有效 UTF-8 文件。\r\n"
    input_dir.mkdir()
    valid_path.write_bytes(valid_text.encode("utf-8"))
    invalid_path.write_bytes(b"\xff")

    with pytest.raises(RuntimeError) as exc_info:
        build_file_plan(
            source_path=invalid_path,
            input_dir=input_dir,
            output_dir=output_dir,
            max_chars=1000,
            max_file_size=100_000,
        )

    assert isinstance(
        exc_info.value.__cause__,
        UnicodeDecodeError,
    )

    plans, failures = pipeline.build_plans_safely(
        source_paths=[invalid_path, valid_path],
        input_dir=input_dir,
        output_dir=output_dir,
        max_chars=1000,
        max_file_size=100_000,
        log_dir=log_dir,
    )

    assert len(plans) == 1
    assert plans[0].relative_path == Path("valid.md")
    assert "".join(plans[0].chunks) == valid_text
    assert failures == [
        {
            "file": "invalid.md",
            "error": (
                f"无法识别文件编码："
                f"{invalid_path.resolve()}"
            ),
        }
    ]
    assert "planning_failed" in read_text(
        log_dir / "batch.jsonl"
    )


def test_crlf_source_runs_through_pipeline_and_cache_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    source_path = input_dir / "structured.md"
    source_text = (
        "# 端到端测试\r\n\r\n"
        "普通段落包含中文、café 和 emoji 🚀。\r\n\r\n"
        "- 列表项一\r\n"
        "  - 嵌套列表项\r\n\r\n"
        "> 引用内容\r\n\r\n"
        "```python\r\n"
        "print('CRLF 保真')\r\n"
        "```\r\n\r\n"
        "| 列 | 值 |\r\n"
        "| --- | --- |\r\n"
        "| 中文 | naïve |\r\n"
    )
    raw_content = source_text.encode("utf-8")
    prompt_text = "保留全部内容。\r\n保留 Markdown 结构。"
    lf_prompt_text = prompt_text.replace("\r\n", "\n")
    plain_prompt = tmp_path / "prompt.md"
    bom_prompt = tmp_path / "prompt-bom.md"
    lf_prompt = tmp_path / "prompt-lf.md"
    input_dir.mkdir()
    source_path.write_bytes(raw_content)
    plain_prompt.write_bytes(prompt_text.encode("utf-8"))
    bom_prompt.write_bytes(
        b"\xef\xbb\xbf" + prompt_text.encode("utf-8")
    )
    lf_prompt.write_bytes(lf_prompt_text.encode("utf-8"))

    config = _make_config(tmp_path)
    config.max_chars = 10_000
    config.system_prompt = read_text(plain_prompt)
    config.prompt_sha256 = sha256_text(
        config.system_prompt
    )
    crlf_prompt_sha256 = config.prompt_sha256
    client = RecordingApiClient(
        [
            "Cleaned CRLF source",
            "Cleaned after prompt change",
            "Cleaned after source change",
        ]
    )
    assembly_sources: list[str] = []
    _install_real_pipeline_flow(
        monkeypatch,
        config,
        client,
        assembly_sources,
    )

    assert _exit_code(lambda: pipeline.main([])) == 0

    crlf_plan = build_file_plan(
        source_path=source_path,
        input_dir=config.input_dir,
        output_dir=config.output_dir,
        max_chars=config.max_chars,
        max_file_size=config.max_file_size,
    )
    expected_message = build_user_message(
        chunk=source_text,
        part_number=1,
        total_parts=1,
        relative_path=Path("structured.md"),
    )

    assert crlf_plan.chunks == [source_text]
    assert crlf_plan.source_chars == len(source_text)
    assert crlf_plan.source_sha256 == hashlib.sha256(
        raw_content
    ).hexdigest()
    assert client.calls[0]["user_message"] == expected_message
    assert source_text in str(client.calls[0]["user_message"])
    assert assembly_sources == [source_text]

    _, chunk_metadata_path, _ = get_chunk_paths(
        crlf_plan.output_dir,
        crlf_plan.source_path,
        1,
    )
    chunk_metadata = json.loads(
        read_text(chunk_metadata_path)
    )
    _, final_metadata_path = assembly.build_final_paths(
        crlf_plan
    )
    final_metadata = json.loads(
        read_text(final_metadata_path)
    )

    assert chunk_metadata["source_sha256"] == (
        crlf_plan.source_sha256
    )
    assert chunk_metadata["chunk_sha256"] == sha256_text(
        source_text
    )
    assert chunk_metadata["prompt_sha256"] == (
        crlf_prompt_sha256
    )
    assert final_metadata["source_sha256"] == (
        crlf_plan.source_sha256
    )
    assert final_metadata["prompt_sha256"] == (
        crlf_prompt_sha256
    )
    assert not pipeline.plan_needs_processing(
        plan=crlf_plan,
        prompt_sha256=crlf_prompt_sha256,
        model=config.model,
        base_url=config.base_url,
    )

    config.system_prompt = read_text(bom_prompt)
    config.prompt_sha256 = sha256_text(
        config.system_prompt
    )

    assert config.system_prompt == prompt_text
    assert config.prompt_sha256 == crlf_prompt_sha256
    assert _exit_code(lambda: pipeline.main([])) == 0
    assert len(client.calls) == 1
    assert assembly_sources == [source_text]

    config.system_prompt = read_text(lf_prompt)
    config.prompt_sha256 = sha256_text(
        config.system_prompt
    )

    assert config.prompt_sha256 != crlf_prompt_sha256
    assert _exit_code(lambda: pipeline.main([])) == 0
    assert len(client.calls) == 2
    assert assembly_sources == [source_text, source_text]

    changed_prompt_metadata = json.loads(
        read_text(chunk_metadata_path)
    )
    assert changed_prompt_metadata["prompt_sha256"] == (
        config.prompt_sha256
    )

    lf_source_text = source_text.replace("\r\n", "\n")
    source_path.write_bytes(lf_source_text.encode("utf-8"))

    assert _exit_code(lambda: pipeline.main([])) == 0

    lf_plan = build_file_plan(
        source_path=source_path,
        input_dir=config.input_dir,
        output_dir=config.output_dir,
        max_chars=config.max_chars,
        max_file_size=config.max_file_size,
    )

    assert len(client.calls) == 3
    assert assembly_sources[-1] == lf_source_text
    assert lf_plan.source_sha256 != crlf_plan.source_sha256
    assert "".join(lf_plan.chunks) == lf_source_text
    assert lf_source_text.replace("\n", "") == (
        source_text.replace("\r\n", "")
    )
    assert client.calls[-1]["user_message"] == build_user_message(
        chunk=lf_source_text,
        part_number=1,
        total_parts=1,
        relative_path=Path("structured.md"),
    )
    assert not pipeline.plan_needs_processing(
        plan=lf_plan,
        prompt_sha256=config.prompt_sha256,
        model=config.model,
        base_url=config.base_url,
    )


def test_failed_crlf_chunk_is_retried_while_reusing_good_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    source_path = input_dir / "retry.md"
    source_text = (
        "First CRLF paragraph.\r\n\r\n"
        "Second CRLF paragraph.\r\n"
    )
    input_dir.mkdir()
    source_path.write_bytes(source_text.encode("utf-8"))
    config = _make_config(tmp_path)
    config.max_chars = 30
    config.system_prompt = "Retry prompt\r\nKeep CRLF."
    config.prompt_sha256 = sha256_text(
        config.system_prompt
    )
    client = RecordingApiClient(
        [
            RuntimeError("transient API failure"),
            "Clean second",
        ]
    )
    assembly_sources: list[str] = []
    _install_real_pipeline_flow(
        monkeypatch,
        config,
        client,
        assembly_sources,
    )
    plan = build_file_plan(
        source_path=source_path,
        input_dir=config.input_dir,
        output_dir=config.output_dir,
        max_chars=config.max_chars,
        max_file_size=config.max_file_size,
    )

    assert len(plan.chunks) == 2
    assert "".join(plan.chunks) == source_text
    assert _exit_code(lambda: pipeline.main([])) == 2
    assert len(client.calls) == 2
    assert client.calls[0]["user_message"] == build_user_message(
        chunk=plan.chunks[0],
        part_number=1,
        total_parts=2,
        relative_path=plan.relative_path,
    )
    assert client.calls[1]["user_message"] == build_user_message(
        chunk=plan.chunks[1],
        part_number=2,
        total_parts=2,
        relative_path=plan.relative_path,
    )
    assert assembly_sources == []

    first_output, first_metadata, _ = get_chunk_paths(
        plan.output_dir,
        plan.source_path,
        1,
    )
    second_output, second_metadata, _ = get_chunk_paths(
        plan.output_dir,
        plan.source_path,
        2,
    )

    assert not first_output.exists()
    assert not first_metadata.exists()
    assert second_output.is_file()
    assert second_metadata.is_file()
    cached_output = second_output.read_bytes()
    cached_metadata = second_metadata.read_bytes()

    client.responses.append("Clean first")

    assert _exit_code(lambda: pipeline.main([])) == 0
    assert len(client.calls) == 3
    assert client.calls[2]["user_message"] == build_user_message(
        chunk=plan.chunks[0],
        part_number=1,
        total_parts=2,
        relative_path=plan.relative_path,
    )
    assert second_output.read_bytes() == cached_output
    assert second_metadata.read_bytes() == cached_metadata
    assert assembly_sources == [source_text]
    assert not pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256=config.prompt_sha256,
        model=config.model,
        base_url=config.base_url,
    )
