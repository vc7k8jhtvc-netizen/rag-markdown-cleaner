from __future__ import annotations

import json
from pathlib import Path

import clean_auto.pipeline as pipeline
from clean_auto.config import FilePlan, sha256_text


def make_plan(tmp_path: Path) -> FilePlan:
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
        relative_path=Path("测试教材.md"),
        source_sha256=sha256_text(
            source_text
        ),
        source_chars=len(source_text),
        chunks=[source_text],
        output_dir=output_dir,
    )


def write_valid_final_output(
    plan: FilePlan,
    prompt_sha256: str,
    model: str,
    base_url: str,
) -> tuple[Path, Path]:
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
        "source_file": (
            plan.relative_path.as_posix()
        ),
        "source_sha256": (
            plan.source_sha256
        ),
        "prompt_sha256": prompt_sha256,
        "model": model,
        "base_url": base_url.rstrip("/"),
        "part_count": len(plan.chunks),
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

    return final_path, metadata_path


def test_missing_final_output_requires_processing(
    tmp_path: Path,
    monkeypatch,
) -> None:
    plan = make_plan(tmp_path)

    monkeypatch.setattr(
        pipeline,
        "plan_has_pending_chunks",
        lambda **kwargs: False,
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
    plan = make_plan(tmp_path)
    prompt_sha256 = "prompt-hash"
    model = "test-model"
    base_url = "https://example.com/v1"

    write_valid_final_output(
        plan=plan,
        prompt_sha256=prompt_sha256,
        model=model,
        base_url=base_url,
    )

    monkeypatch.setattr(
        pipeline,
        "plan_has_pending_chunks",
        lambda **kwargs: False,
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
    plan = make_plan(tmp_path)
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
        "文件已被手动修改\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pipeline,
        "plan_has_pending_chunks",
        lambda **kwargs: False,
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
    plan = make_plan(tmp_path)

    final_path = (
        plan.output_dir
        / "测试教材_cleaned.md"
    )
    final_path.write_text(
        "# 测试教材\n\n正文\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(
        pipeline,
        "plan_has_pending_chunks",
        lambda **kwargs: False,
    )

    assert pipeline.plan_needs_processing(
        plan=plan,
        prompt_sha256="prompt-hash",
        model="test-model",
        base_url="https://example.com/v1",
    )
