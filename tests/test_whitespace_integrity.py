from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import clean_auto.assembly as assembly
import clean_auto.processor as processor
from clean_auto.assembly import build_final_paths
from clean_auto.chunking import (
    build_expected_metadata,
    build_output_metadata,
    get_chunk_paths,
    is_completed_chunk,
    plan_has_pending_chunks,
    save_partial_response,
)
from clean_auto.config import (
    FilePlan,
    RequestResult,
    RuntimeConfig,
    sha256_text,
)
from clean_auto.pipeline import final_output_is_current


class NoUnexpectedApiClient:
    def __init__(self, responses: list[str] | None = None) -> None:
        self.responses = responses or []
        self.calls: list[dict[str, object]] = []

    def stream_request(self, **kwargs: object) -> RequestResult:
        self.calls.append(kwargs)
        response = self.responses[len(self.calls) - 1]
        return RequestResult(
            text=response,
            elapsed_seconds=0.0,
            received_events=1,
            received_chars=len(response),
        )


def _make_plan(tmp_path: Path, chunks: list[str]) -> FilePlan:
    source_text = "\n\n".join(chunks)
    source_path = tmp_path / "input" / "sample.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_text(source_text, encoding="utf-8", newline="")

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


def _expected_metadata(
    plan: FilePlan,
    config: RuntimeConfig,
    part_number: int,
) -> dict[str, object]:
    return build_expected_metadata(
        relative_path=plan.relative_path,
        source_sha256=plan.source_sha256,
        chunk_sha256=sha256_text(plan.chunks[part_number - 1]),
        prompt_sha256=config.prompt_sha256,
        model=config.model,
        base_url=config.base_url,
        part_number=part_number,
        total_parts=len(plan.chunks),
        strict_validation=config.strict_validation,
    )


def _patch_valid_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    report = SimpleNamespace(
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
    monkeypatch.setattr(
        processor,
        "validate_result",
        lambda **_kwargs: ([], []),
    )
    monkeypatch.setattr(
        processor,
        "assess_quality",
        lambda **_kwargs: report,
    )
    monkeypatch.setattr(
        assembly,
        "assess_quality",
        lambda **_kwargs: report,
    )


@pytest.mark.parametrize(
    "result",
    [
        "plain body",
        "plain body\n",
        "plain body\n\n\n",
        "\nplain body",
        "plain body  ",
        "```text\nliteral body\n```",
        "```markdown\n# Heading\n```",
        "```\nplain fenced body\n```",
        "\n```markdown\n\n  body  \n\n```\n\n",
    ],
    ids=[
        "no-trailing-newline",
        "single-trailing-newline",
        "multiple-trailing-newlines",
        "leading-newline",
        "trailing-space",
        "fenced-text",
        "fenced-markdown",
        "fenced-no-language",
        "fenced-meaningful-whitespace",
    ],
)
def test_chunk_cache_preserves_result_bytes_and_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    result: str,
) -> None:
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    output_path, metadata_path, _ = get_chunk_paths(
        plan.output_dir,
        plan.source_path,
        1,
    )
    metadata = build_output_metadata(
        expected=_expected_metadata(plan, config, 1),
        result=result,
        warnings=[],
    )
    metadata["status"] = "completed"

    processor.save_chunk_result(
        output_path=output_path,
        metadata_path=metadata_path,
        result=result,
        metadata=metadata,
    )

    assert output_path.read_bytes() == result.encode("utf-8")
    assert output_path.read_text(encoding="utf-8") == result
    saved_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert saved_metadata["output_sha256"] == sha256_text(result)
    assert saved_metadata["output_chars"] == len(result)
    assert is_completed_chunk(
        output_path,
        metadata_path,
        _expected_metadata(plan, config, 1),
    )
    assert not plan_has_pending_chunks(
        plan=plan,
        prompt_sha256=config.prompt_sha256,
        model=config.model,
        base_url=config.base_url,
    )

    monkeypatch.setattr(
        processor,
        "assemble_completed_file",
        lambda **_kwargs: None,
    )
    client = NoUnexpectedApiClient()
    outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=client,
        initial_consecutive_failures=0,
    )
    assert client.calls == []
    assert outcome.stats.success_parts == 1
    assert outcome.stats.skipped_parts == 1
    assert outcome.stats.failed_parts == 0


def test_legacy_stripped_hash_is_rejected_and_reprocessed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    output_path, metadata_path, _ = get_chunk_paths(
        plan.output_dir,
        plan.source_path,
        1,
    )
    result = "\nold body  \n\n"
    metadata = build_output_metadata(
        expected=_expected_metadata(plan, config, 1),
        result=result.strip(),
        warnings=[],
    )
    metadata["status"] = "completed"
    processor.save_chunk_result(
        output_path=output_path,
        metadata_path=metadata_path,
        result=result,
        metadata=metadata,
    )

    assert not is_completed_chunk(
        output_path,
        metadata_path,
        _expected_metadata(plan, config, 1),
    )

    monkeypatch.setattr(
        processor,
        "assemble_completed_file",
        lambda **_kwargs: None,
    )
    replacement = "\nnew body  \n\n"
    client = NoUnexpectedApiClient([replacement])
    monkeypatch.setattr(
        processor,
        "validate_result",
        lambda **_kwargs: ([], []),
    )
    outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=client,
        initial_consecutive_failures=0,
    )
    assert len(client.calls) == 1
    assert outcome.stats.success_parts == 1
    assert outcome.stats.failed_parts == 0
    assert output_path.read_text(encoding="utf-8") == replacement


def test_process_file_assembles_whitespace_parts_and_reuses_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        "\n# Part one",
        "Part two  ",
        "Part three\n\n\n",
        "```markdown\n# Fenced\n\nBody  \n```\n",
    ]
    plan = _make_plan(tmp_path, responses)
    config = _make_config(tmp_path)
    _patch_valid_processing(monkeypatch)

    first_client = NoUnexpectedApiClient(responses)
    first_outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=first_client,
        initial_consecutive_failures=0,
    )

    final_path, final_metadata_path = build_final_paths(plan)
    expected_final = "\n\n".join(responses)
    assert len(first_client.calls) == len(responses)
    assert first_outcome.stats.success_parts == len(responses)
    assert first_outcome.stats.failed_parts == 0
    assert final_path.read_bytes() == expected_final.encode("utf-8")
    assert final_path.read_text(encoding="utf-8") == expected_final
    final_metadata = json.loads(
        final_metadata_path.read_text(encoding="utf-8")
    )
    assert final_metadata["output_sha256"] == sha256_text(expected_final)
    assert not plan_has_pending_chunks(
        plan=plan,
        prompt_sha256=config.prompt_sha256,
        model=config.model,
        base_url=config.base_url,
    )
    assert final_output_is_current(
        plan=plan,
        prompt_sha256=config.prompt_sha256,
        model=config.model,
        base_url=config.base_url,
    )
    chunk_snapshots: list[tuple[bytes, bytes]] = []
    for part_number, response in enumerate(responses, start=1):
        output_path, metadata_path, _ = get_chunk_paths(
            plan.output_dir,
            plan.source_path,
            part_number,
        )
        assert output_path.read_bytes() == response.encode("utf-8")
        chunk_snapshots.append(
            (output_path.read_bytes(), metadata_path.read_bytes())
        )

    second_client = NoUnexpectedApiClient()
    second_outcome = processor.process_file(
        plan=plan,
        file_index=1,
        total_files=1,
        config=config,
        client=second_client,
        initial_consecutive_failures=0,
    )
    assert second_client.calls == []
    assert second_outcome.stats.success_parts == len(responses)
    assert second_outcome.stats.skipped_parts == len(responses)
    assert second_outcome.stats.failed_parts == 0
    for part_number, snapshot in enumerate(chunk_snapshots, start=1):
        output_path, metadata_path, _ = get_chunk_paths(
            plan.output_dir,
            plan.source_path,
            part_number,
        )
        assert (output_path.read_bytes(), metadata_path.read_bytes()) == snapshot
    assert final_path.read_bytes() == expected_final.encode("utf-8")
    assert final_path.read_text(encoding="utf-8") == expected_final


def test_partial_response_keeps_result_whitespace(
    tmp_path: Path,
) -> None:
    partial_path = tmp_path / "sample.partial.md"
    result = "\npartial body  \n\n"

    save_partial_response(
        partial_path=partial_path,
        text=result,
        reason="request interrupted",
    )

    payload = partial_path.read_text(encoding="utf-8")
    assert payload.endswith(result)
