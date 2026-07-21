from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .chunking import (
    build_expected_metadata,
    get_chunk_paths,
    is_completed_chunk,
)
from .config import (
    FilePlan,
    RuntimeConfig,
    atomic_write_json,
    atomic_write_text,
    now_iso,
    read_text,
    safe_name,
    sha256_text,
)
from .quality import assess_quality


def build_final_paths(
    plan: FilePlan,
) -> tuple[Path, Path]:
    filename_stem = safe_name(
        plan.source_path.stem
    )

    final_path = (
        plan.output_dir
        / f"{filename_stem}_cleaned.md"
    )

    metadata_path = (
        plan.output_dir
        / f"{filename_stem}_cleaned.md.meta.json"
    )

    return final_path, metadata_path


def _remove_front_matter(
    text: str,
) -> str:
    """
    删除后续分片开头重复的 YAML Front Matter。

    Front Matter 不完整时不删除，避免误删正文。
    """
    stripped = text.lstrip(
        "\ufeff \t\r\n"
    )

    if not stripped.startswith("---"):
        return text.strip()

    lines = stripped.splitlines()

    if not lines or lines[0].strip() != "---":
        return text.strip()

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(
                lines[index + 1:]
            ).strip()

    return text.strip()


def _read_part_metadata(
    metadata_path: Path,
) -> dict[str, Any]:
    try:
        data = json.loads(
            read_text(metadata_path)
        )
    except Exception as exc:
        raise RuntimeError(
            "无法读取分片 metadata："
            f"{metadata_path}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            "分片 metadata 不是对象："
            f"{metadata_path}"
        )

    return data


def _collect_part_warnings(
    part_metadata: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []

    for metadata in part_metadata:
        part_warnings = metadata.get(
            "warnings",
            [],
        )

        if not isinstance(part_warnings, list):
            part_warnings = [str(part_warnings)]

        if part_warnings:
            warnings.append(
                {
                    "part": metadata.get(
                        "part_number"
                    ),
                    "warnings": part_warnings,
                }
            )

    return warnings


def _read_existing_text(
    path: Path,
) -> str | None:
    if not path.is_file():
        return None

    return read_text(path)


def _restore_file(
    path: Path,
    previous_text: str | None,
) -> None:
    if previous_text is None:
        try:
            path.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        return

    atomic_write_text(
        path,
        previous_text,
    )


def _publish_final_output(
    final_path: Path,
    metadata_path: Path,
    final_text: str,
    final_metadata: dict[str, Any],
) -> None:
    """
    原子发布完整文件。

    写入失败时恢复发布前的完整文件与 metadata。
    """
    previous_final = _read_existing_text(
        final_path
    )
    previous_metadata = _read_existing_text(
        metadata_path
    )

    try:
        atomic_write_json(
            metadata_path,
            final_metadata,
        )

        atomic_write_text(
            final_path,
            final_text,
        )

    except Exception as publish_error:
        restore_errors: list[str] = []

        try:
            _restore_file(
                final_path,
                previous_final,
            )
        except Exception as exc:
            restore_errors.append(
                "恢复旧完整文件失败："
                f"{exc}"
            )

        try:
            _restore_file(
                metadata_path,
                previous_metadata,
            )
        except Exception as exc:
            restore_errors.append(
                "恢复旧 metadata 失败："
                f"{exc}"
            )

        if restore_errors:
            raise RuntimeError(
                "发布完整文件失败，并且回滚不完整："
                + "；".join(restore_errors)
            ) from publish_error

        raise RuntimeError(
            "发布完整文件失败，"
            "已恢复发布前的旧版本"
        ) from publish_error


def _build_review_paths(
    plan: FilePlan,
    config: RuntimeConfig,
) -> tuple[Path, Path]:
    """
    为需要人工复核的完整文件创建稳定路径。

    review 目录会保持输入文件的子目录结构，
    并使用路径哈希避免同名文件冲突。
    """
    review_root = (
        config.base_dir
        / "review"
    ).resolve()

    path_hash = sha256_text(
        plan.relative_path.as_posix()
    )[:10]

    directory_name = (
        f"{safe_name(plan.source_path.stem)}_"
        f"{path_hash}"
    )

    review_dir = (
        review_root
        / plan.relative_path.parent
        / directory_name
    ).resolve()

    if not review_dir.is_relative_to(
        review_root
    ):
        raise RuntimeError(
            "复核目录超出 review 根目录："
            f"{review_dir}"
        )

    filename_stem = safe_name(
        plan.source_path.stem
    )

    review_document = (
        review_dir
        / f"{filename_stem}_cleaned.md"
    )

    review_report = (
        review_dir
        / f"{filename_stem}_review.json"
    )

    return review_document, review_report


def _remove_review_copy(
    review_document: Path,
    review_report: Path,
) -> None:
    """
    当前结果不再需要复核时，删除旧复核副本。
    """
    for path in (
        review_document,
        review_report,
    ):
        try:
            path.unlink(
                missing_ok=True
            )
        except OSError as exc:
            raise RuntimeError(
                "无法清理旧复核文件："
                f"{path}"
            ) from exc


def sync_review_copy(
    plan: FilePlan,
    config: RuntimeConfig,
    final_path: Path,
    final_text: str,
    final_metadata: dict[str, Any],
) -> Path | None:
    """
    同步 review 目录。

    - review_required=true：复制完整 Markdown 和复核报告；
    - review_required=false：删除该文件旧复核副本；
    - 返回复核 Markdown 路径，或 None。
    """
    review_document, review_report = (
        _build_review_paths(
            plan,
            config,
        )
    )

    review_required = bool(
        final_metadata.get(
            "review_required",
            False,
        )
    )

    if not review_required:
        _remove_review_copy(
            review_document,
            review_report,
        )
        return None

    try:
        relative_final_output = (
            final_path.resolve()
            .relative_to(
                config.base_dir.resolve()
            )
            .as_posix()
        )
    except ValueError:
        relative_final_output = final_path.name

    review_report_data = {
        "version": 1,
        "status": "review_required",
        "source_file": (
            plan.relative_path.as_posix()
        ),
        "final_output": relative_final_output,
        "source_sha256": (
            final_metadata.get(
                "source_sha256"
            )
        ),
        "output_sha256": (
            final_metadata.get(
                "output_sha256"
            )
        ),
        "output_chars": (
            final_metadata.get(
                "output_chars"
            )
        ),
        "quality": final_metadata.get(
            "quality",
            {},
        ),
        "part_warnings": final_metadata.get(
            "part_warnings",
            [],
        ),
        "created_at": now_iso(),
    }

    atomic_write_text(
        review_document,
        final_text,
    )

    atomic_write_json(
        review_report,
        review_report_data,
    )

    return review_document


def assemble_completed_file(
    plan: FilePlan,
    config: RuntimeConfig,
) -> tuple[Path, Path]:
    """
    验证分片、合并完整文件、检查质量并安全发布。

    质量检查失败时不会删除旧完整文件。
    """
    total_parts = len(plan.chunks)

    if total_parts <= 0:
        raise RuntimeError(
            "文件没有可合并的分片："
            f"{plan.relative_path}"
        )

    final_path, final_metadata_path = (
        build_final_paths(plan)
    )

    part_texts: list[str] = []
    part_metadata: list[
        dict[str, Any]
    ] = []

    for part_number, chunk in enumerate(
        plan.chunks,
        start=1,
    ):
        (
            output_path,
            metadata_path,
            _,
        ) = get_chunk_paths(
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
            total_parts=total_parts,
        )

        if not is_completed_chunk(
            output_path,
            metadata_path,
            expected,
        ):
            raise RuntimeError(
                "无法生成完整文件，"
                f"第 {part_number}/{total_parts} "
                "个分片尚未完成"
            )

        part_text = read_text(
            output_path
        ).strip()

        if not part_text:
            raise RuntimeError(
                "分片内容为空："
                f"{output_path}"
            )

        metadata = _read_part_metadata(
            metadata_path
        )

        if part_number > 1:
            part_text = _remove_front_matter(
                part_text
            )

        if part_text:
            part_texts.append(part_text)

        part_metadata.append(metadata)

    final_text = "\n\n".join(
        part_texts
    ).strip()

    if not final_text:
        raise RuntimeError(
            "合并结果为空："
            f"{plan.relative_path}"
        )

    final_text += "\n"

    source_text = read_text(
        plan.source_path
    )

    quality = assess_quality(
        input_text=source_text,
        output_text=final_text,
    )

    if quality.severe_errors:
        raise RuntimeError(
            "完整文件质量检查失败："
            + "；".join(
                quality.severe_errors
            )
            + "。旧完整文件已保留。"
        )

    part_warnings = _collect_part_warnings(
        part_metadata
    )

    review_required = (
        quality.review_required
        or any(
            bool(
                metadata.get(
                    "review_required",
                    False,
                )
            )
            for metadata in part_metadata
        )
    )

    final_metadata = {
        "version": 6,
        "status": "completed",
        "source_file": (
            plan.relative_path.as_posix()
        ),
        "source_sha256": (
            plan.source_sha256
        ),
        "prompt_sha256": (
            config.prompt_sha256
        ),
        "model": config.model,
        "base_url": (
            config.base_url.rstrip("/")
        ),
        "part_count": total_parts,
        "output_sha256": sha256_text(
            final_text
        ),
        "output_chars": len(
            final_text
        ),
        "review_required": (
            review_required
        ),
        "quality": quality.to_dict(),
        "part_warnings": part_warnings,
        "completed_at": now_iso(),
    }

    _publish_final_output(
        final_path=final_path,
        metadata_path=final_metadata_path,
        final_text=final_text,
        final_metadata=final_metadata,
    )

    try:
        review_path = sync_review_copy(
            plan=plan,
            config=config,
            final_path=final_path,
            final_text=final_text,
            final_metadata=final_metadata,
        )

        if review_path is not None:
            print(
                "[人工复核] 已复制到："
                f"{review_path}"
            )

    except Exception as exc:
        print(
            "[复核目录警告] "
            "无法同步 review 副本："
            f"{exc}"
        )

    print(
        "[完整文件质量] "
        "保留比例："
        f"{quality.retained_ratio:.1%}；"
        "删除比例："
        f"{quality.removed_ratio:.1%}"
    )

    if quality.review_required:
        print(
            "[完整文件需要复核] "
            "合并结果存在质量提示。"
        )

        if quality.warnings:
            print(
                "[完整文件提示] "
                + "；".join(
                    quality.warnings
                )
            )

    return (
        final_path,
        final_metadata_path,
    )
