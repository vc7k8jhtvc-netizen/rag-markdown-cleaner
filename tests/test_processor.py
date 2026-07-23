from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import clean_auto.processor as processor
from clean_auto.chunking import (
    build_expected_metadata,
    build_output_metadata,
    get_chunk_paths,
)
from clean_auto.config import (
    MAX_CONSECUTIVE_FAILURES,
    FilePlan,
    RequestResult,
    RuntimeConfig,
    sha256_text,
)
from clean_auto.progress import ProgressReporter, format_progress_event


class FakeClient:
    def __init__(
        self,
        responses: list[str | Exception],
    ) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    def stream_request(
        self,
        **kwargs: object,
    ) -> RequestResult:
        self.calls.append(kwargs)
        reporter = kwargs.get("reporter")
        context = kwargs.get("context")
        if isinstance(reporter, ProgressReporter) and context is not None:
            reporter.file_event(context, "chunk_started")
        response = self.responses[len(self.calls) - 1]

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
    chunks: list[str],
) -> FilePlan:
    source_path = tmp_path / "input" / "sample.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_text = "\n\n".join(chunks)
    source_path.write_text(source_text, encoding="utf-8")

    return FilePlan(
        source_path=source_path,
        relative_path=Path("sample.md"),
        source_sha256=sha256_text(source_text),
        source_chars=len(source_text),
        chunks=chunks,
        output_dir=tmp_path / "output" / "sample",
    )


def _make_config(tmp_path: Path) -> RuntimeConfig:
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
    )


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


def _patch_successful_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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


def _write_completed_chunk(
    plan: FilePlan,
    config: RuntimeConfig,
    part_number: int,
    result: str,
) -> None:
    output_path, metadata_path, _ = get_chunk_paths(
        plan.output_dir,
        plan.source_path,
        part_number,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    chunk = plan.chunks[part_number - 1]
    expected = build_expected_metadata(
        relative_path=plan.relative_path,
        source_sha256=plan.source_sha256,
        chunk_sha256=sha256_text(chunk),
        prompt_sha256=config.prompt_sha256,
        model=config.model,
        base_url=config.base_url,
        part_number=part_number,
        total_parts=len(plan.chunks),
        strict_validation=config.strict_validation,
    )
    metadata = build_output_metadata(
        expected=expected,
        result=result,
        warnings=[],
    )
    metadata["status"] = "completed"
    output_path.write_text(result + "\n", encoding="utf-8")
    metadata_path.write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )


@pytest.mark.parametrize("failed_write", ["output", "metadata"])
def test_save_chunk_result_removes_both_files_on_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_write: str,
) -> None:
    """Cover paired cleanup when either half of a chunk commit fails."""
    output_path = tmp_path / "part.md"
    metadata_path = tmp_path / "part.md.meta.json"
    output_path.write_text("old output", encoding="utf-8")
    metadata_path.write_text("old metadata", encoding="utf-8")

    def write_output(path: Path, text: str) -> None:
        if failed_write == "output":
            raise OSError("output write failed")
        path.write_text(text, encoding="utf-8")

    def write_metadata(
        path: Path,
        data: dict[str, object],
    ) -> None:
        if failed_write == "metadata":
            raise OSError("metadata write failed")
        path.write_text(json.dumps(data), encoding="utf-8")

    monkeypatch.setattr(
        processor,
        "atomic_write_text",
        write_output,
    )
    monkeypatch.setattr(
        processor,
        "atomic_write_json",
        write_metadata,
    )

    with pytest.raises(OSError):
        processor.save_chunk_result(
            output_path=output_path,
            metadata_path=metadata_path,
            result="new output",
            metadata={"status": "completed"},
        )

    assert not output_path.exists()
    assert not metadata_path.exists()


def test_save_chunk_result_preserves_meaningful_outer_whitespace(
    tmp_path: Path,
) -> None:
    """Chunk persistence must not normalize a valid fenced Markdown result."""
    output_path = tmp_path / "part.md"
    metadata_path = tmp_path / "part.md.meta.json"
    result = "\n```markdown\nbody\n```\n\n"

    processor.save_chunk_result(
        output_path,
        metadata_path,
        result,
        {"status": "completed"},
    )

    assert output_path.read_text(encoding="utf-8") == result


def test_successful_file_processing_commits_chunks_then_assembles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the successful API-to-metadata flow and assembly gate."""
    plan = _make_plan(tmp_path, ["source-1", "source-2"])
    config = _make_config(tmp_path)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    partial_paths: list[Path] = []

    for part_number in (1, 2):
        _, _, partial_path = get_chunk_paths(
            plan.output_dir,
            plan.source_path,
            part_number,
        )
        partial_path.write_text("partial", encoding="utf-8")
        partial_paths.append(partial_path)

    client = FakeClient(
        [
            "```markdown\nClean one\n```",
            "Clean two",
        ]
    )
    _patch_successful_checks(monkeypatch)
    assembly_calls: list[FilePlan] = []

    def fake_assemble(
        plan: FilePlan,
        config: RuntimeConfig,
    ) -> tuple[Path, Path]:
        del config
        assembly_calls.append(plan)
        return (
            plan.output_dir / "sample_cleaned.md",
            plan.output_dir / "sample_cleaned.md.meta.json",
        )

    monkeypatch.setattr(
        processor,
        "assemble_completed_file",
        fake_assemble,
    )

    outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=client,
        initial_consecutive_failures=2,
    )

    assert len(client.calls) == 2
    assert assembly_calls == [plan]
    assert outcome.stats.total_parts == 2
    assert outcome.stats.success_parts == 2
    assert outcome.stats.failed_parts == 0
    assert outcome.consecutive_failures == 0
    assert all(not path.exists() for path in partial_paths)

    first_output, first_metadata, _ = get_chunk_paths(
        plan.output_dir,
        plan.source_path,
        1,
    )
    assert first_output.read_text(encoding="utf-8") == "```markdown\nClean one\n```"
    metadata = json.loads(first_metadata.read_text(encoding="utf-8"))
    assert metadata["status"] == "completed"
    assert metadata["schema"] == "rag-cleaner/chunk-metadata"
    assert metadata["output_sha256"] == sha256_text(
        "```markdown\nClean one\n```"
    )


def test_completed_chunks_skip_api_but_still_trigger_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover resume behavior when chunks exist but the final file needs rebuilding."""
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    _write_completed_chunk(plan, config, 1, "Clean result")
    client = FakeClient([AssertionError("API must not be called")])
    assembly_calls: list[FilePlan] = []
    reporter = ProgressReporter()

    def fake_assemble(
        plan: FilePlan,
        config: RuntimeConfig,
        **_kwargs: object,
    ) -> tuple[Path, Path]:
        del config
        assembly_calls.append(plan)
        return (
            plan.output_dir / "sample_cleaned.md",
            plan.output_dir / "sample_cleaned.md.meta.json",
        )

    monkeypatch.setattr(
        processor,
        "assemble_completed_file",
        fake_assemble,
    )

    outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=client,
        initial_consecutive_failures=3,
        reporter=reporter,
    )

    assert client.calls == []
    assert assembly_calls == [plan]
    assert outcome.stats.success_parts == 1
    assert outcome.stats.skipped_parts == 1
    assert outcome.stats.failed_parts == 0
    assert outcome.consecutive_failures == 0
    assert [event.kind for event in reporter.drain()] == ["chunk_skipped"]


def test_any_chunk_failure_prevents_final_assembly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the rule that a partial file can never be assembled as complete."""
    plan = _make_plan(tmp_path, ["source-1", "source-2"])
    config = _make_config(tmp_path)
    client = FakeClient(
        [RuntimeError("API failed"), "Clean two"]
    )
    _patch_successful_checks(monkeypatch)
    assembly_calls: list[object] = []
    monkeypatch.setattr(
        processor,
        "assemble_completed_file",
        lambda **kwargs: assembly_calls.append(kwargs),
    )

    outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=client,
        initial_consecutive_failures=0,
    )

    assert len(client.calls) == 2
    assert assembly_calls == []
    assert outcome.stats.success_parts == 1
    assert outcome.stats.failed_parts == 1
    assert not outcome.stopped


def test_inherited_failure_count_can_trigger_automatic_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover batch-level consecutive failures carried into file processing."""
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    client = FakeClient([RuntimeError("API failed")])
    assembly_calls: list[object] = []
    monkeypatch.setattr(
        processor,
        "assemble_completed_file",
        lambda **kwargs: assembly_calls.append(kwargs),
    )

    outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=client,
        initial_consecutive_failures=(
            MAX_CONSECUTIVE_FAILURES - 1
        ),
    )

    assert outcome.stopped
    assert outcome.stats.failed_parts == 1
    assert outcome.consecutive_failures == MAX_CONSECUTIVE_FAILURES
    assert assembly_calls == []


def test_chunk_request_and_quality_messages_are_contextual_worker_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    client = FakeClient(["cleaned"])
    reporter = ProgressReporter()
    warning = "输出保留比例低于复核阈值 70%，建议人工检查删除内容"
    monkeypatch.setattr(processor, "validate_result", lambda **_kwargs: ([], []))
    monkeypatch.setattr(
        processor,
        "assess_quality",
        lambda **_kwargs: SimpleNamespace(
            severe_errors=[],
            review_required=True,
            warnings=[warning],
            retained_ratio=0.6,
            removed_ratio=0.4,
            to_dict=lambda: {
                "severe_errors": [],
                "warnings": [warning],
                "review_required": True,
            },
        ),
    )
    monkeypatch.setattr(
        processor,
        "assemble_completed_file",
        lambda **_kwargs: (tmp_path / "final.md", tmp_path / "final.json"),
    )

    outcome = processor.process_file(
        plan=plan,
        file_index=2,
        total_files=3,
        config=config,
        client=client,
        initial_consecutive_failures=0,
        reporter=reporter,
    )

    assert outcome.stats.success_parts == 1
    assert capsys.readouterr().out == ""
    events = reporter.drain()
    assert [event.kind for event in events] == [
        "chunk_started",
        "quality_warning",
        "quality_warning",
        "chunk_completed",
    ]
    lines = [format_progress_event(event) for event in events]
    assert lines[0] == (
        "[2/3] 处理中：sample.md（分片 1/1，正在请求并等待模型返回）"
    )
    assert lines[1] == "[2/3] 质量提示：sample.md（分片 1/1，需要人工复核）"
    assert lines[2] == f"[2/3] 质量提示：sample.md（分片 1/1，{warning}）"
    assert lines[3] == "[2/3] 分片完成：sample.md（分片 1/1）"
    assert all(not line.startswith("[提示]") for line in lines)


def test_failed_chunk_emits_contextual_sanitized_event_and_continues(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    plan = _make_plan(tmp_path, ["source-1", "source-2"])
    config = _make_config(tmp_path)
    client = FakeClient(
        [
            RuntimeError(
                "Authorization: Bearer secret-token\n"
                f"failed at {tmp_path / 'private' / 'response.json'}"
            ),
            "cleaned-2",
        ]
    )
    reporter = ProgressReporter()
    _patch_successful_checks(monkeypatch)
    monkeypatch.setattr(processor, "assemble_completed_file", lambda **_kwargs: None)

    outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=client,
        initial_consecutive_failures=0,
        reporter=reporter,
    )

    events = reporter.drain()
    failed = [event for event in events if event.kind == "chunk_failed"]
    assert len(failed) == 1
    assert failed[0].part_number == 1
    assert failed[0].total_parts == 2
    assert failed[0].relative_path == Path("sample.md")
    assert "secret-token" not in (failed[0].error or "")
    assert str(tmp_path) not in (failed[0].error or "")
    assert "\n" not in (failed[0].error or "")
    assert any(event.kind == "chunk_completed" and event.part_number == 2 for event in events)
    assert outcome.stats.failed_parts == 1
    assert outcome.stats.success_parts == 1
    assert capsys.readouterr().out == ""


def test_strict_mode_does_not_reuse_lenient_chunk_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, ["source"])
    lenient_config = _make_config(tmp_path)
    _write_completed_chunk(plan, lenient_config, 1, "cached result")
    strict_config = _make_config(tmp_path)
    strict_config.strict_validation = True
    client = FakeClient(["strict result"])
    _patch_successful_checks(monkeypatch)
    monkeypatch.setattr(processor, "assemble_completed_file", lambda **_kwargs: None)

    outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=strict_config,
        client=client,
        initial_consecutive_failures=0,
    )

    assert len(client.calls) == 1
    assert outcome.stats.skipped_parts == 0
    assert outcome.stats.success_parts == 1
