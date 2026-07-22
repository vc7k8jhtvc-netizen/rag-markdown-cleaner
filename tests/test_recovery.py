from __future__ import annotations

import json
from pathlib import Path

import clean_auto.pipeline as pipeline
from clean_auto.chunking import (
    build_expected_metadata,
    build_output_metadata,
    get_chunk_paths,
)
from clean_auto.config import (
    FilePlan,
    sha256_text,
)


def make_plan(
    tmp_path: Path,
) -> FilePlan:
    """
    创建一个临时教材处理计划。
    """
    source_path = (
        tmp_path
        / "input"
        / "测试教材.md"
    )

    source_path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    source_text = (
        "# 测试教材\n\n"
        "这是教材正文。"
    )

    source_path.write_text(
        source_text,
        encoding="utf-8",
    )

    output_dir = (
        tmp_path
        / "output"
        / "测试教材_test"
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    return FilePlan(
        source_path=source_path,
        relative_path=Path(
            "测试教材.md"
        ),
        source_sha256=sha256_text(
            source_text
        ),
        source_chars=len(
            source_text
        ),
        chunks=[
            source_text
        ],
        output_dir=output_dir,
    )


def write_valid_final_output(
    plan: FilePlan,
    prompt_sha256: str,
    model: str,
    base_url: str,
) -> tuple[Path, Path]:
    """
    写入一组符合当前完整文件 metadata 规则的测试数据。
    """
    final_text = (
        "---\n"
        "title: 测试教材\n"
        "subject: 安全生产管理\n"
        "source: null\n"
        "type: 教材\n"
        "year: null\n"
        "status: OCR清洗完成\n"
        "---\n\n"
        "# 测试教材\n\n"
        "这是教材正文。\n"
    )

    final_path = (
        plan.output_dir
        / "测试教材_cleaned.md"
    )

    metadata_path = (
        plan.output_dir
        / "测试教材_cleaned.md.meta.json"
    )

    final_path.write_text(
        final_text,
        encoding="utf-8",
    )

    metadata = {
        "version": 5,
        "status": "completed",
        "source_file": (
            plan.relative_path.as_posix()
        ),
        "source_sha256": (
            plan.source_sha256
        ),
        "prompt_sha256": (
            prompt_sha256
        ),
        "model": model,
        "base_url": (
            base_url.rstrip("/")
        ),
        "part_count": len(
            plan.chunks
        ),
        "output_sha256": sha256_text(
            final_text
        ),
    }

    metadata_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return (
        final_path,
        metadata_path,
    )


def write_completed_chunks(
    plan: FilePlan,
    prompt_sha256: str,
    model: str,
    base_url: str,
) -> list[tuple[Path, Path]]:
    """Write chunk pairs accepted by the real pending-chunk check."""
    completed_paths: list[tuple[Path, Path]] = []

    for part_number, chunk in enumerate(
        plan.chunks,
        start=1,
    ):
        output_path, metadata_path, _ = get_chunk_paths(
            plan.output_dir,
            plan.source_path,
            part_number,
        )
        result = f"cleaned part {part_number}"
        expected = build_expected_metadata(
            relative_path=plan.relative_path,
            source_sha256=plan.source_sha256,
            chunk_sha256=sha256_text(chunk),
            prompt_sha256=prompt_sha256,
            model=model,
            base_url=base_url,
            part_number=part_number,
            total_parts=len(plan.chunks),
        )
        metadata = build_output_metadata(
            expected=expected,
            result=result,
            warnings=[],
        )
        metadata["status"] = "completed"
        output_path.write_text(
            result + "\n",
            encoding="utf-8",
        )
        metadata_path.write_text(
            json.dumps(metadata),
            encoding="utf-8",
        )
        completed_paths.append(
            (output_path, metadata_path)
        )

    return completed_paths


def disable_pending_chunk_check(
    monkeypatch,
) -> None:
    """
    模拟所有模型分片均已完成。

    这样测试只关注最终完整文件的恢复判断，
    不会调用模型，也不会产生 API 请求。
    """
    monkeypatch.setattr(
        pipeline,
        "plan_has_pending_chunks",
        lambda **kwargs: False,
    )


def test_missing_final_output_requires_processing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(
        tmp_path
    )

    disable_pending_chunk_check(
        monkeypatch
    )

    assert pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256="prompt-hash",
        model="test-model",
        base_url="https://example.com/v1",
    )


def test_valid_final_output_is_complete(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(
        tmp_path
    )

    prompt_sha256 = "prompt-hash"
    model = "test-model"
    base_url = "https://example.com/v1"

    write_valid_final_output(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )

    disable_pending_chunk_check(
        monkeypatch
    )

    assert not pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )


def test_modified_final_output_requires_processing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(
        tmp_path
    )

    prompt_sha256 = "prompt-hash"
    model = "test-model"
    base_url = "https://example.com/v1"

    final_path, _ = (
        write_valid_final_output(
            plan=plan,
            prompt_sha256=prompt_sha256,
            model=model,
            base_url=base_url,
        )
    )

    final_path.write_text(
        "文件已经被手动修改。\n",
        encoding="utf-8",
    )

    disable_pending_chunk_check(
        monkeypatch
    )

    assert pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )


def test_missing_final_metadata_requires_processing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(
        tmp_path
    )

    final_path = (
        plan.output_dir
        / "测试教材_cleaned.md"
    )

    final_path.write_text(
        "# 测试教材\n\n正文\n",
        encoding="utf-8",
    )

    disable_pending_chunk_check(
        monkeypatch
    )

    assert pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256="prompt-hash",
        model="test-model",
        base_url="https://example.com/v1",
    )


def test_non_completed_status_requires_processing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(
        tmp_path
    )

    prompt_sha256 = "prompt-hash"
    model = "test-model"
    base_url = "https://example.com/v1"

    _, metadata_path = (
        write_valid_final_output(
            plan=plan,
            prompt_sha256=prompt_sha256,
            model=model,
            base_url=base_url,
        )
    )

    metadata = json.loads(
        metadata_path.read_text(
            encoding="utf-8",
        )
    )

    metadata["status"] = "incomplete"

    metadata_path.write_text(
        json.dumps(
            metadata,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    disable_pending_chunk_check(
        monkeypatch
    )

    assert pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )


def test_real_recovery_accepts_completed_chunks_and_current_final(
    tmp_path: Path,
) -> None:
    """Cover the complete recovery path without replacing chunk checks."""
    plan = make_plan(tmp_path)
    prompt_sha256 = "prompt-hash"
    model = "test-model"
    base_url = "https://example.com/v1"
    write_completed_chunks(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )
    write_valid_final_output(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )

    assert not pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )


def test_real_pending_chunk_overrides_current_final_output(
    tmp_path: Path,
) -> None:
    """Cover pending chunk detection taking precedence over a valid final file."""
    plan = make_plan(tmp_path)
    prompt_sha256 = "prompt-hash"
    model = "test-model"
    base_url = "https://example.com/v1"
    completed_paths = write_completed_chunks(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )
    write_valid_final_output(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )
    completed_paths[0][0].unlink()

    assert pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )


def test_real_completed_chunks_require_missing_final_to_be_reassembled(
    tmp_path: Path,
) -> None:
    """Cover reassembly when all chunks are current but the final file is absent."""
    plan = make_plan(tmp_path)
    prompt_sha256 = "prompt-hash"
    model = "test-model"
    base_url = "https://example.com/v1"
    write_completed_chunks(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )

    assert pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )
