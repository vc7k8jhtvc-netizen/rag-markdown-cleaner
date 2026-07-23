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
    sha256_file,
    sha256_text,
)
from .metadata_schema import (
    FINAL_METADATA_SCHEMA,
    FINAL_METADATA_SCHEMA_VERSION,
    REVIEW_REPORT_SCHEMA,
    REVIEW_REPORT_SCHEMA_VERSION,
    add_schema_identity,
)
from .quality import assess_quality
from .progress import ProgressContext, ProgressReporter
from .validation import (
    FRONT_MATTER_PATTERN,
    parse_front_matter_with_error,
    validate_front_matter_fields,
)


def build_final_paths(
    plan: FilePlan,
) -> tuple[Path, Path]:
    """
    返回完整清洗文件和完整文件 metadata 的路径。
    """
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


def _progress_path(path: Path, base_dir: Path) -> str:
    try:
        return path.resolve().relative_to(base_dir.resolve()).as_posix()
    except (OSError, ValueError):
        return path.name


def _remove_front_matter(
    text: str,
) -> str:
    """
    删除后续分片开头重复的 YAML Front Matter。

    只处理明确以以下结构开头的内容：

    ---
    ...
    ---

    Front Matter 没有正确闭合时保留原文，
    避免误删教材正文。
    """
    content_start = 0
    while (
        content_start < len(text)
        and text[content_start]
        in "\ufeff \t\r\n"
    ):
        content_start += 1

    candidate = text[content_start:]

    match = FRONT_MATTER_PATTERN.match(
        candidate
    )

    if match is None:
        return text

    fields, yaml_error = (
        parse_front_matter_with_error(
            match.group(1)
        )
    )

    if yaml_error is not None:
        return text

    field_errors, _ = (
        validate_front_matter_fields(
            fields,
            strict=True,
        )
    )

    if field_errors:
        return text

    return (
        text[:content_start]
        + candidate[match.end():]
    )


def _read_part_metadata(
    metadata_path: Path,
) -> dict[str, Any]:
    """
    读取并检查分片 metadata。
    """
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
    """
    汇总所有分片的质量提示。
    """
    warnings: list[dict[str, Any]] = []

    for metadata in part_metadata:
        part_warnings = metadata.get(
            "warnings",
            [],
        )

        if not isinstance(
            part_warnings,
            list,
        ):
            part_warnings = [
                str(part_warnings)
            ]

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
    """
    读取发布前的文件内容，用于失败回滚。
    """
    if not path.is_file():
        return None

    return read_text(path)


def _restore_file(
    path: Path,
    previous_text: str | None,
) -> None:
    """
    恢复文件到发布前状态。

    原文件不存在时，删除本次产生的新文件。
    """
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
    安全发布完整文件和 metadata。

    新结果在质量检查通过后才进入此函数。

    如果写入失败，尽可能恢复发布前的完整文件
    和 metadata，避免旧的可用结果丢失。
    """
    previous_final = _read_existing_text(
        final_path
    )

    previous_metadata = (
        _read_existing_text(
            metadata_path
        )
    )

    try:
        # 先写 metadata，再写正文。
        #
        # 如果两个操作之间进程异常退出，
        # metadata 与正文哈希会不一致，
        # 下次运行会自动重新合并。
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
                "发布完整文件失败，"
                "并且回滚不完整："
                + "；".join(
                    restore_errors
                )
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

    保持输入文件的子目录结构，并通过路径哈希
    避免不同目录中的同名文件发生冲突。
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

    return (
        review_document,
        review_report,
    )


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

    review_required=true：

    - 写入完整 Markdown 复核副本；
    - 写入 JSON 复核报告。

    review_required=false：

    - 清理此前存在的旧复核副本。
    """
    (
        review_document,
        review_report,
    ) = _build_review_paths(
        plan,
        config,
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

    except (
        OSError,
        ValueError,
    ):
        relative_final_output = (
            final_path.name
        )

    review_report_data: dict[
        str,
        Any,
    ] = {
        # 保留旧 version 字段，兼容已有报告。
        "version": 1,
        "status": "review_required",
        "source_file": (
            plan.relative_path.as_posix()
        ),
        "final_output": (
            relative_final_output
        ),
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
        "quality": (
            final_metadata.get(
                "quality",
                {},
            )
        ),
        "part_warnings": (
            final_metadata.get(
                "part_warnings",
                [],
            )
        ),
        "created_at": now_iso(),
    }

    add_schema_identity(
        metadata=review_report_data,
        schema=REVIEW_REPORT_SCHEMA,
        schema_version=(
            REVIEW_REPORT_SCHEMA_VERSION
        ),
    )

    try:
        atomic_write_text(
            review_document,
            final_text,
        )

        atomic_write_json(
            review_report,
            review_report_data,
        )

    except Exception:
        # 复核副本必须成对存在。
        try:
            review_document.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        try:
            review_report.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise

    return review_document


def assemble_completed_file(
    plan: FilePlan,
    config: RuntimeConfig,
    reporter: ProgressReporter | None = None,
    context: ProgressContext | None = None,
) -> tuple[Path, Path]:
    """
    验证分片、合并完整文件、检查质量并安全发布。

    处理规则：

    - 任意分片未完成时不合并；
    - 第一片完整保留；
    - 后续分片重复 Front Matter 会被移除；
    - 候选完整文件先在内存中生成；
    - 候选文件先通过完整质量检查；
    - 质量失败时保留此前完整文件；
    - 所有检查通过后才发布新完整文件；
    - 高风险结果同步到 review 目录。
    """
    total_parts = len(
        plan.chunks
    )

    if total_parts <= 0:
        raise RuntimeError(
            "文件没有可合并的分片："
            f"{plan.relative_path}"
        )

    (
        final_path,
        final_metadata_path,
    ) = build_final_paths(
        plan
    )

    part_texts: list[str] = []

    part_metadata: list[
        dict[str, Any]
    ] = []

    # --------------------------------------------------------
    # 验证并读取全部分片
    # --------------------------------------------------------

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
            source_sha256=(
                plan.source_sha256
            ),
            chunk_sha256=sha256_text(
                chunk
            ),
            prompt_sha256=(
                config.prompt_sha256
            ),
            model=config.model,
            base_url=config.base_url,
            part_number=part_number,
            total_parts=total_parts,
            strict_validation=config.strict_validation,
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

        part_text = read_text(output_path)

        if not part_text.strip():
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
            part_texts.append(
                part_text
            )

        part_metadata.append(
            metadata
        )

    # --------------------------------------------------------
    # 生成候选完整文件
    # --------------------------------------------------------

    final_text = "\n\n".join(part_texts)

    if not final_text:
        raise RuntimeError(
            "合并结果为空："
            f"{plan.relative_path}"
        )

    # --------------------------------------------------------
    # 完整文件质量检查
    #
    # 此处尚未修改旧完整文件。
    # --------------------------------------------------------

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

    part_warnings = (
        _collect_part_warnings(
            part_metadata
        )
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

    # --------------------------------------------------------
    # 构造完整文件 metadata
    # --------------------------------------------------------

    final_metadata: dict[
        str,
        Any,
    ] = {
        # 保留旧 version 字段，兼容已有结果。
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
        "strict_validation": config.strict_validation,
        "normalization_policy": "preserve-outer-fence-v1",
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
        "part_warnings": (
            part_warnings
        ),
        "completed_at": now_iso(),
    }

    add_schema_identity(
        metadata=final_metadata,
        schema=FINAL_METADATA_SCHEMA,
        schema_version=(
            FINAL_METADATA_SCHEMA_VERSION
        ),
    )

    # --------------------------------------------------------
    # 所有检查通过后安全发布。
    # --------------------------------------------------------

    if sha256_file(plan.source_path) != plan.source_sha256:
        raise RuntimeError(
            "源文件在处理期间发生变化，拒绝发布完整输出 "
            f"(SHA-256 不一致)：{plan.relative_path}"
        )

    _publish_final_output(
        final_path=final_path,
        metadata_path=(
            final_metadata_path
        ),
        final_text=final_text,
        final_metadata=(
            final_metadata
        ),
    )

    # --------------------------------------------------------
    # 同步人工复核副本。
    #
    # 复核副本失败不会影响正式完整文件。
    # --------------------------------------------------------

    try:
        review_path = sync_review_copy(
            plan=plan,
            config=config,
            final_path=final_path,
            final_text=final_text,
            final_metadata=(
                final_metadata
            ),
        )

        if review_path is not None and reporter is not None:
            display_path = _progress_path(review_path, config.base_dir)
            if context is not None:
                reporter.file_event(
                    context,
                    "detail",
                    message=f"人工复核副本已复制到：{display_path}",
                )
            else:
                reporter.notice(f"人工复核副本已复制到：{display_path}")

    except Exception:
        if reporter is not None and context is not None:
            reporter.file_event(
                context,
                "detail",
                message="无法同步 review 副本",
            )
        elif reporter is not None:
            reporter.notice("无法同步 review 副本")

    if quality.review_required:
        if reporter is not None and context is not None:
            reporter.file_event(
                context,
                "quality_warning",
                message="完整文件需要人工复核",
            )
        elif reporter is not None:
            reporter.notice("完整文件存在质量提示，需要人工复核。")

    if quality.warnings:
        if reporter is not None and context is not None:
            reporter.file_event(
                context,
                "quality_warning",
                message="；".join(quality.warnings),
            )
        elif reporter is not None:
            reporter.notice("；".join(quality.warnings))

    return (
        final_path,
        final_metadata_path,
    )
