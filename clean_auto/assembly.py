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

    只处理明确以：

    ---
    ...
    ---

    开头的内容。

    如果 Front Matter 没有正确闭合，则保留原文，
    不擅自删除内容。
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

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            remaining = lines[index + 1:]

            return "\n".join(
                remaining
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
            f"无法读取分片 metadata："
            f"{metadata_path}"
        ) from exc

    if not isinstance(data, dict):
        raise RuntimeError(
            f"分片 metadata 不是对象："
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


def assemble_completed_file(
    plan: FilePlan,
    config: RuntimeConfig,
) -> tuple[Path, Path]:
    """
    验证所有分片后，合并生成完整 Markdown。

    处理规则：

    - 任意分片未完成，直接失败；
    - 第一片保留原样；
    - 后续分片的重复 Front Matter 会被移除；
    - 合并结果会与完整源文件做质量比较；
    - 严重质量异常时不生成最终文件；
    - 最终文件和 metadata 使用原子写入。
    """
    total_parts = len(plan.chunks)

    if total_parts <= 0:
        raise RuntimeError(
            f"文件没有可合并的分片："
            f"{plan.relative_path}"
        )

    part_texts: list[str] = []
    part_metadata: list[dict[str, Any]] = []

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
                f"分片内容为空："
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
            f"合并结果为空："
            f"{plan.relative_path}"
        )

    final_text += "\n"

    # 使用完整源文件，而不是某个分片，
    # 检查合并后的整体内容是否异常减少。
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
        )

    final_path, final_metadata_path = (
        build_final_paths(plan)
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
        "version": 3,
        "source_file": (
            plan.relative_path.as_posix()
        ),
        "source_sha256": plan.source_sha256,
        "prompt_sha256": config.prompt_sha256,
        "model": config.model,
        "base_url": config.base_url.rstrip("/"),
        "part_count": total_parts,
        "output_sha256": sha256_text(
            final_text
        ),
        "output_chars": len(final_text),
        "review_required": review_required,
        "quality": quality.to_dict(),
        "part_warnings": part_warnings,
        "completed_at": now_iso(),
    }

    try:
        atomic_write_text(
            final_path,
            final_text,
        )

        atomic_write_json(
            final_metadata_path,
            final_metadata,
        )

    except Exception:
        try:
            final_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        try:
            final_metadata_path.unlink(
                missing_ok=True
            )
        except OSError:
            pass

        raise

    print(
        "[完整文件质量] "
        f"保留比例："
        f"{quality.retained_ratio:.1%}；"
        f"删除比例："
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

    return final_path, final_metadata_path
