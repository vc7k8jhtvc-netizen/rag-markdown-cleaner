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


def _remove_front_matter(
    text: str,
) -> str:
    """
    删除后续分片开头重复的 YAML Front Matter。

    只处理明确以以下结构开头的内容：

    ---
    ...
    ---

    如果 Front Matter 没有正确闭合，
    则保留原文，不擅自删除。
    """
    stripped = text.lstrip(
        "\ufeff \t\r\n"
    )

    if not stripped.startswith("---"):
        return text.strip()

    lines = stripped.splitlines()

    if not lines:
        return text.strip()

    if lines[0].strip() != "---":
        return text.strip()

    for index in range(
        1,
        len(lines),
    ):
        if lines[index].strip() == "---":
            remaining = lines[
                index + 1:
            ]

            return "\n".join(
                remaining
            ).strip()

    # YAML 没有闭合时不删除任何内容。
    return text.strip()


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
    读取已有文件，用于发布失败时恢复旧版本。

    文件不存在时返回 None。
    """
    if not path.is_file():
        return None

    return read_text(path)


def _restore_file(
    path: Path,
    previous_text: str | None,
) -> None:
    """
    恢复发布前的文件状态。

    - 原文件存在：恢复原内容；
    - 原文件不存在：删除本次产生的新文件。
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

    发布顺序：

    1. 保存旧文件内容；
    2. 原子写入新 metadata；
    3. 原子替换新完整文件；
    4. 任意步骤失败时恢复旧版本。

    metadata 先写、正文后写的原因：

    如果程序在两个替换步骤之间突然退出，
    旧完整正文仍然保留，不会先丢失用户可阅读的旧稿。

    此时 metadata 与正文哈希不匹配，
    下次运行会自动重新合并。
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


def assemble_completed_file(
    plan: FilePlan,
    config: RuntimeConfig,
) -> tuple[Path, Path]:
    """
    验证所有分片后，合并生成完整 Markdown。

    处理规则：

    - 任意分片未完成时不合并；
    - 第一片完整保留；
    - 后续分片重复的 Front Matter 会被移除；
    - 合并结果先在内存中完成；
    - 发布前执行完整文档质量检查；
    - 质量检查失败时保留旧完整文件；
    - 新结果全部通过后才替换旧完整文件；
    - 发布失败时尽量恢复旧完整文件和 metadata。
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
            part_texts.append(
                part_text
            )

        part_metadata.append(
            metadata
        )

    # --------------------------------------------------------
    # 构造候选完整文件
    # --------------------------------------------------------

    final_text = "\n\n".join(
        part_texts
    ).strip()

    if not final_text:
        raise RuntimeError(
            "合并结果为空："
            f"{plan.relative_path}"
        )

    final_text += "\n"

    # --------------------------------------------------------
    # 完整文档质量检查
    #
    # 重要：这里尚未修改旧完整文件。
    # 如果检查失败，旧完整文件会继续保留。
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

    # --------------------------------------------------------
    # 构造完整文件 metadata
    # --------------------------------------------------------

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
        "version": 5,
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

    # --------------------------------------------------------
    # 所有检查通过后，才发布候选完整文件。
    # --------------------------------------------------------

    _publish_final_output(
        final_path=final_path,
        metadata_path=final_metadata_path,
        final_text=final_text,
        final_metadata=final_metadata,
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
