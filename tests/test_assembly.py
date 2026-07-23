from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import clean_auto.assembly as assembly
from clean_auto.assembly import (
    _remove_front_matter,
)
from clean_auto.chunking import (
    build_expected_metadata,
    build_file_plan,
    build_output_metadata,
    get_chunk_paths,
)
from clean_auto.config import (
    FilePlan,
    RuntimeConfig,
    atomic_write_text,
    sha256_text,
)
from clean_auto.progress import ProgressContext, ProgressReporter, format_progress_event


FRONT_MATTER = (
    "---\n"
    "title: Example\n"
    "subject: null\n"
    "source: null\n"
    "type: material\n"
    "year: 2025\n"
    "status: OCR\u6e05\u6d17\u5b8c\u6210\n"
    "---"
)


def _make_plan(
    tmp_path: Path,
    chunks: list[str],
    relative_path: Path = Path("sample.md"),
) -> FilePlan:
    source_path = tmp_path / "input" / "sample.md"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_text = "\n\n".join(chunks)
    source_path.write_bytes(source_text.encode("utf-8"))

    return FilePlan(
        source_path=source_path,
        relative_path=relative_path,
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


def _quality_report(
    severe_errors: list[str] | None = None,
) -> SimpleNamespace:
    errors = severe_errors or []
    return SimpleNamespace(
        severe_errors=errors,
        review_required=False,
        warnings=[],
        retained_ratio=1.0,
        removed_ratio=0.0,
        to_dict=lambda: {
            "severe_errors": errors,
            "warnings": [],
            "review_required": False,
        },
    )


def _write_completed_parts(
    plan: FilePlan,
    config: RuntimeConfig,
    results: list[str],
) -> None:
    plan.output_dir.mkdir(parents=True, exist_ok=True)

    for part_number, (chunk, result) in enumerate(
        zip(plan.chunks, results, strict=True),
        start=1,
    ):
        output_path, metadata_path, _ = get_chunk_paths(
            plan.output_dir,
            plan.source_path,
            part_number,
        )
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
        atomic_write_text(output_path, result)
        metadata_path.write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )


def test_horizontal_rules_are_not_removed() -> None:
    text = (
        "---\n"
        "必须保留的教材正文\n"
        "---\n\n"
        "后续正文"
    )

    assert _remove_front_matter(text) == text


def test_valid_repeated_front_matter_is_removed() -> None:
    text = (
        "---\n"
        "title: 测试教材\n"
        "subject: 安全生产管理\n"
        "source: null\n"
        "type: 教材\n"
        "year: 2025\n"
        "status: OCR清洗完成\n"
        "---\n\n"
        "后续正文"
    )

    assert _remove_front_matter(text) == (
        "后续正文"
    )


def test_incomplete_front_matter_is_preserved() -> None:
    """Cover preservation of text that only resembles complete metadata."""
    text = (
        "---\n"
        "title: 测试教材\n"
        "---\n\n"
        "后续正文"
    )

    assert _remove_front_matter(text) == text


def test_completed_parts_are_assembled_in_order(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover ordered merge, first metadata retention, and later metadata removal."""
    plan = _make_plan(tmp_path, ["source-1", "source-2", "source-3"])
    config = _make_config(tmp_path)
    results = [
        f"{FRONT_MATTER}\n\nFirst body",
        f"{FRONT_MATTER}\n\nSecond body",
        "Third body",
    ]
    _write_completed_parts(plan, config, results)
    monkeypatch.setattr(
        assembly,
        "assess_quality",
        lambda **_kwargs: _quality_report(),
    )
    monkeypatch.setattr(
        assembly,
        "sync_review_copy",
        lambda **_kwargs: None,
    )

    final_path, metadata_path = assembly.assemble_completed_file(
        plan=plan,
        config=config,
    )

    expected_text = (
        f"{FRONT_MATTER}\n\nFirst body\n\n"
            "Second body\n\nThird body"
    )
    assert final_path.read_text(encoding="utf-8") == expected_text
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["part_count"] == 3
    assert metadata["output_sha256"] == sha256_text(expected_text)
    assert metadata["schema"] == "rag-cleaner/final-metadata"


def test_quality_check_receives_complete_crlf_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_dir = tmp_path / "input"
    source_path = input_dir / "sample.md"
    source_text = (
        "# 标题\r\n\r\n"
        "第一段正文。\r\n\r\n"
        "第二段正文。\r\n"
    )
    input_dir.mkdir()
    source_path.write_bytes(
        source_text.encode("utf-8")
    )
    plan = build_file_plan(
        source_path=source_path,
        input_dir=input_dir,
        output_dir=tmp_path / "output",
        max_chars=1000,
        max_file_size=100_000,
    )
    config = _make_config(tmp_path)
    _write_completed_parts(
        plan,
        config,
        ["清洗后的正文。"],
    )
    quality_inputs: list[str] = []

    def capture_quality(
        input_text: str,
        output_text: str,
    ) -> SimpleNamespace:
        del output_text
        quality_inputs.append(input_text)
        return _quality_report()

    monkeypatch.setattr(
        assembly,
        "assess_quality",
        capture_quality,
    )
    monkeypatch.setattr(
        assembly,
        "sync_review_copy",
        lambda **_kwargs: None,
    )

    assembly.assemble_completed_file(
        plan=plan,
        config=config,
    )

    assert quality_inputs == [source_text]


@pytest.mark.parametrize(
    "failure_kind",
    ["missing", "corrupt_metadata", "empty"],
)
def test_incomplete_part_never_replaces_existing_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: str,
) -> None:
    """Cover publish gating for missing, corrupt, and empty chunk artifacts."""
    plan = _make_plan(tmp_path, ["source-1", "source-2"])
    config = _make_config(tmp_path)
    _write_completed_parts(plan, config, ["Part one", "Part two"])
    second_output, second_metadata, _ = get_chunk_paths(
        plan.output_dir,
        plan.source_path,
        2,
    )

    if failure_kind == "missing":
        second_output.unlink()
        second_metadata.unlink()
    elif failure_kind == "corrupt_metadata":
        second_metadata.write_text("not-json", encoding="utf-8")
    else:
        second_output.write_text("", encoding="utf-8")

    final_path, final_metadata_path = assembly.build_final_paths(plan)
    final_path.write_text("old final", encoding="utf-8")
    final_metadata_path.write_text("old metadata", encoding="utf-8")
    monkeypatch.setattr(
        assembly,
        "assess_quality",
        lambda **_kwargs: pytest.fail("quality check must not run"),
    )

    with pytest.raises(RuntimeError):
        assembly.assemble_completed_file(plan=plan, config=config)

    assert final_path.read_text(encoding="utf-8") == "old final"
    assert final_metadata_path.read_text(encoding="utf-8") == "old metadata"


def test_quality_rejection_preserves_existing_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover rejection before publication when complete-file quality is severe."""
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    _write_completed_parts(plan, config, ["candidate"])
    final_path, metadata_path = assembly.build_final_paths(plan)
    final_path.write_text("old final", encoding="utf-8")
    metadata_path.write_text("old metadata", encoding="utf-8")
    publish_calls: list[object] = []
    monkeypatch.setattr(
        assembly,
        "assess_quality",
        lambda **_kwargs: _quality_report(["content loss"]),
    )
    monkeypatch.setattr(
        assembly,
        "_publish_final_output",
        lambda **kwargs: publish_calls.append(kwargs),
    )

    with pytest.raises(RuntimeError):
        assembly.assemble_completed_file(plan=plan, config=config)

    assert publish_calls == []
    assert final_path.read_text(encoding="utf-8") == "old final"
    assert metadata_path.read_text(encoding="utf-8") == "old metadata"


def test_source_change_before_publish_preserves_existing_final_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    _write_completed_parts(plan, config, ["candidate"])
    final_path, metadata_path = assembly.build_final_paths(plan)
    final_path.write_text("old final", encoding="utf-8")
    metadata_path.write_text("old metadata", encoding="utf-8")

    def change_source(**_kwargs: object) -> SimpleNamespace:
        plan.source_path.write_bytes(b"changed after planning\n")
        return _quality_report()

    monkeypatch.setattr(assembly, "assess_quality", change_source)
    monkeypatch.setattr(
        assembly,
        "_publish_final_output",
        lambda **_kwargs: pytest.fail("changed source must not be published"),
    )

    with pytest.raises(RuntimeError, match="SHA-256"):
        assembly.assemble_completed_file(plan=plan, config=config)

    assert final_path.read_text(encoding="utf-8") == "old final"
    assert metadata_path.read_text(encoding="utf-8") == "old metadata"


@pytest.mark.parametrize("failure_stage", ["metadata", "document"])
def test_publish_failure_restores_both_previous_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    """Cover two-file rollback when either publication write fails."""
    final_path = tmp_path / "final.md"
    metadata_path = tmp_path / "final.md.meta.json"
    final_path.write_text("old final", encoding="utf-8")
    metadata_path.write_text("old metadata", encoding="utf-8")
    original_write_text = assembly.atomic_write_text

    if failure_stage == "metadata":
        monkeypatch.setattr(
            assembly,
            "atomic_write_json",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                OSError("metadata write failed")
            ),
        )
    else:
        def fail_new_document(path: Path, text: str) -> None:
            if path == final_path and text == "new final":
                raise OSError("document write failed")
            original_write_text(path, text)

        monkeypatch.setattr(
            assembly,
            "atomic_write_text",
            fail_new_document,
        )

    with pytest.raises(RuntimeError):
        assembly._publish_final_output(
            final_path=final_path,
            metadata_path=metadata_path,
            final_text="new final",
            final_metadata={"status": "completed"},
        )

    assert final_path.read_text(encoding="utf-8") == "old final"
    assert metadata_path.read_text(encoding="utf-8") == "old metadata"


def test_publish_failure_removes_new_files_when_no_previous_version_exists(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover rollback cleanup for a first publication with no old version."""
    final_path = tmp_path / "final.md"
    metadata_path = tmp_path / "final.md.meta.json"
    original_write_text = assembly.atomic_write_text

    def fail_new_document(path: Path, text: str) -> None:
        if path == final_path and text == "new final":
            raise OSError("document write failed")
        original_write_text(path, text)

    monkeypatch.setattr(
        assembly,
        "atomic_write_text",
        fail_new_document,
    )

    with pytest.raises(RuntimeError):
        assembly._publish_final_output(
            final_path=final_path,
            metadata_path=metadata_path,
            final_text="new final",
            final_metadata={"status": "completed"},
        )

    assert not final_path.exists()
    assert not metadata_path.exists()


def test_review_path_cannot_escape_review_root(
    tmp_path: Path,
) -> None:
    """Cover path traversal rejection for review-copy destinations."""
    plan = _make_plan(
        tmp_path,
        ["source"],
        relative_path=Path("..") / "outside.md",
    )

    with pytest.raises(RuntimeError):
        assembly._build_review_paths(
            plan=plan,
            config=_make_config(tmp_path),
        )


def test_complete_file_quality_warning_has_file_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    _write_completed_parts(plan, config, ["cleaned"])
    warning = "输出保留比例低于复核阈值 70%，建议人工检查删除内容"
    monkeypatch.setattr(
        assembly,
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
    monkeypatch.setattr(assembly, "sync_review_copy", lambda **_kwargs: None)
    reporter = ProgressReporter()

    assembly.assemble_completed_file(
        plan=plan,
        config=config,
        reporter=reporter,
        context=ProgressContext(
            file_index=2,
            total_files=4,
            relative_path=plan.relative_path,
        ),
    )

    assert capsys.readouterr().out == ""
    lines = [format_progress_event(event) for event in reporter.drain()]
    assert lines == [
        "[2/4] 质量提示：sample.md（完整文件需要人工复核）",
        f"[2/4] 质量提示：sample.md（{warning}）",
    ]


def test_complete_file_warning_does_not_require_review_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    _write_completed_parts(plan, config, ["cleaned"])
    warning = "输出保留比例低于复核阈值 70%，建议人工检查删除内容"
    monkeypatch.setattr(
        assembly,
        "assess_quality",
        lambda **_kwargs: SimpleNamespace(
            severe_errors=[],
            review_required=False,
            warnings=[warning],
            retained_ratio=0.6,
            removed_ratio=0.4,
            to_dict=lambda: {
                "severe_errors": [],
                "warnings": [warning],
                "review_required": False,
            },
        ),
    )
    monkeypatch.setattr(assembly, "sync_review_copy", lambda **_kwargs: None)
    reporter = ProgressReporter()

    assembly.assemble_completed_file(
        plan=plan,
        config=config,
        reporter=reporter,
        context=ProgressContext(
            file_index=1,
            total_files=1,
            relative_path=plan.relative_path,
        ),
    )

    lines = [format_progress_event(event) for event in reporter.drain()]
    assert lines == [f"[1/1] 质量提示：sample.md（{warning}）"]


def test_review_progress_uses_project_relative_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan = _make_plan(tmp_path, ["source"])
    config = _make_config(tmp_path)
    _write_completed_parts(plan, config, ["cleaned"])
    monkeypatch.setattr(assembly, "assess_quality", lambda **_kwargs: _quality_report())
    review_path = (tmp_path / "review" / "sample_cleaned.md").resolve()
    monkeypatch.setattr(assembly, "sync_review_copy", lambda **_kwargs: review_path)
    reporter = ProgressReporter()

    assembly.assemble_completed_file(
        plan=plan,
        config=config,
        reporter=reporter,
        context=ProgressContext(
            file_index=1,
            total_files=1,
            relative_path=plan.relative_path,
        ),
    )

    line = format_progress_event(reporter.drain()[0])
    assert "review/sample_cleaned.md" in line
    assert str(tmp_path) not in line
