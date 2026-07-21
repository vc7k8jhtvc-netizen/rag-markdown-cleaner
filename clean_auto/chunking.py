from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import (
    FilePlan,
    atomic_write_text,
    now_iso,
    read_text,
    safe_name,
    sha256_file,
    sha256_text,
)


FENCE_START_PATTERN = re.compile(
    r"^[ \t]*(?P<fence>`{3,}|~{3,})"
)

SENTENCE_END_PATTERN = re.compile(
    r"[。！？；.!?;][ \t]*"
)


def is_cleaned_file(path: Path) -> bool:
    return path.stem.lower().endswith(
        "_cleaned"
    )


def ensure_path_inside(
    path: Path,
    base_dir: Path,
    description: str,
) -> Path:
    """
    确保文件解析后的真实路径位于指定目录内。

    这可以防止 input 中的符号链接或目录联接
    指向项目外部文件。
    """
    resolved_path = path.resolve()
    resolved_base = base_dir.resolve()

    if not resolved_path.is_relative_to(
        resolved_base
    ):
        raise RuntimeError(
            f"{description}超出允许目录："
            f"{resolved_path}"
        )

    return resolved_path


def find_input_files(
    input_dir: Path,
) -> list[Path]:
    """
    查找 input 目录中的 Markdown 文件。

    安全规则：

    - 只处理 .md；
    - 跳过以 _cleaned 结尾的文件；
    - 跳过符号链接；
    - 跳过解析后位于 input 外部的文件；
    - 单个不可访问文件不会阻止扫描其他文件。
    """
    if not input_dir.is_dir():
        return []

    root = input_dir.resolve()
    files: list[Path] = []

    for path in input_dir.rglob("*"):
        try:
            if path.is_symlink():
                continue

            if not path.is_file():
                continue

            if path.suffix.lower() != ".md":
                continue

            if is_cleaned_file(path):
                continue

            resolved = path.resolve()

            if not resolved.is_relative_to(root):
                continue

            files.append(resolved)

        except OSError:
            continue

    return sorted(
        files,
        key=lambda item: item.as_posix().lower(),
    )


def _fence_marker(
    line: str,
) -> tuple[str, int] | None:
    """
    返回 Markdown 围栏的字符和长度。

    支持：

    ```python
    内容
    ```

    以及波浪线围栏。
    """
    match = FENCE_START_PATTERN.match(line)

    if not match:
        return None

    fence = match.group("fence")

    return fence[0], len(fence)


def _is_fence_close(
    line: str,
    marker_char: str,
    marker_length: int,
) -> bool:
    stripped = line.strip()

    if not stripped:
        return False

    if not stripped.startswith(
        marker_char * marker_length
    ):
        return False

    return all(
        character == marker_char
        for character in stripped
    )


def split_by_paragraphs(
    text: str,
) -> list[str]:
    """
    将 Markdown 拆成结构块。

    与普通的空行正则切分相比，这个函数会：

    - 保留围栏代码块内部的空行；
    - 尽量将连续表格行保留在同一块；
    - 保留 YAML Front Matter；
    - 将普通连续文本作为一个结构块；
    - 不把代码块内部内容误认为普通段落。
    """
    lines = text.splitlines()
    blocks: list[str] = []
    index = 0
    total_lines = len(lines)

    while index < total_lines:
        line = lines[index]

        if not line.strip():
            index += 1
            continue

        # 只在文档开头识别 YAML Front Matter。
        if (
            not blocks
            and index == 0
            and line.lstrip("\ufeff").strip()
            == "---"
        ):
            front_matter_lines = [line]
            index += 1

            while index < total_lines:
                current = lines[index]
                front_matter_lines.append(current)
                index += 1

                if current.strip() == "---":
                    break

            blocks.append(
                "\n".join(front_matter_lines)
            )
            continue

        fence_info = _fence_marker(line)

        if fence_info is not None:
            marker_char, marker_length = (
                fence_info
            )
            fenced_lines = [line]
            index += 1

            while index < total_lines:
                current = lines[index]
                fenced_lines.append(current)
                index += 1

                if _is_fence_close(
                    current,
                    marker_char,
                    marker_length,
                ):
                    break

            blocks.append(
                "\n".join(fenced_lines)
            )
            continue

        normal_lines = [line]
        index += 1

        while index < total_lines:
            current = lines[index]

            if not current.strip():
                break

            # 新代码围栏作为独立结构块处理。
            if _fence_marker(current) is not None:
                break

            normal_lines.append(current)
            index += 1

        blocks.append(
            "\n".join(normal_lines)
        )

    return [
        block.strip()
        for block in blocks
        if block.strip()
    ]


def _find_preferred_split(
    text: str,
    max_chars: int,
) -> int:
    """
    在最大字符限制内寻找自然切分位置。

    优先顺序：

    1. 中文或英文句末标点；
    2. 空格或制表符；
    3. 最大字符位置。

    为避免产生特别短的分片，只接受位于窗口后半段的
    自然边界。
    """
    if len(text) <= max_chars:
        return len(text)

    window = text[:max_chars]
    minimum_position = max(
        1,
        int(max_chars * 0.5),
    )

    sentence_positions = [
        match.end()
        for match in SENTENCE_END_PATTERN.finditer(
            window
        )
        if match.end() >= minimum_position
    ]

    if sentence_positions:
        return sentence_positions[-1]

    whitespace_positions = [
        index + 1
        for index, character in enumerate(window)
        if (
            character in {" ", "\t"}
            and index + 1 >= minimum_position
        )
    ]

    if whitespace_positions:
        return whitespace_positions[-1]

    return max_chars


def split_line_by_chars(
    line: str,
    max_chars: int,
) -> list[str]:
    """
    拆分超过限制的单行文本。

    优先在句末或空白边界切分，最后才按字符硬切。
    """
    if max_chars <= 0:
        raise ValueError(
            "max_chars 必须大于 0"
        )

    if len(line) <= max_chars:
        return [line]

    parts: list[str] = []
    remaining = line

    while len(remaining) > max_chars:
        split_position = _find_preferred_split(
            remaining,
            max_chars,
        )

        if split_position <= 0:
            split_position = max_chars

        part = remaining[:split_position]
        remaining = remaining[split_position:]

        if part:
            parts.append(part)

    if remaining:
        parts.append(remaining)

    return parts


def split_long_block(
    block: str,
    max_chars: int,
) -> list[str]:
    """
    拆分超过限制的 Markdown 结构块。

    优先按完整行切分：

    - 表格不会在普通单元格位置随意切断；
    - 标题不会在长度未超限时被切断；
    - 列表项尽量保持完整；
    - 只有单行本身超过限制时才进一步拆分。
    """
    if len(block) <= max_chars:
        return [block]

    parts: list[str] = []
    current_lines: list[str] = []
    current_length = 0

    for line in block.splitlines():
        line_parts = split_line_by_chars(
            line,
            max_chars,
        )

        for line_part in line_parts:
            line_length = len(line_part)
            separator_length = (
                1 if current_lines else 0
            )

            if (
                current_lines
                and (
                    current_length
                    + separator_length
                    + line_length
                    > max_chars
                )
            ):
                parts.append(
                    "\n".join(current_lines)
                )
                current_lines = []
                current_length = 0

            if current_lines:
                current_length += 1

            current_lines.append(line_part)
            current_length += line_length

    if current_lines:
        parts.append(
            "\n".join(current_lines)
        )

    result = [
        part.strip()
        for part in parts
        if part.strip()
    ]

    oversized = [
        len(part)
        for part in result
        if len(part) > max_chars
    ]

    if oversized:
        raise RuntimeError(
            "长结构块切分失败，仍存在超长内容："
            f"{max(oversized):,} 字符"
        )

    return result


def create_chunks(
    text: str,
    max_chars: int,
) -> list[str]:
    """
    创建发送给模型的 Markdown 分片。

    处理原则：

    - 优先保持 Markdown 结构块；
    - 标题和后续正文在容量允许时放在同一分片；
    - 表格、列表和连续段落尽量保持完整；
    - 代码围栏中的空行不会造成错误拆分；
    - 任何最终分片都不能超过 max_chars。
    """
    if max_chars <= 0:
        raise ValueError(
            "max_chars 必须大于 0"
        )

    chunks: list[str] = []
    current_blocks: list[str] = []
    current_length = 0

    def flush_current() -> None:
        nonlocal current_blocks
        nonlocal current_length

        if current_blocks:
            chunks.append(
                "\n\n".join(current_blocks)
            )
            current_blocks = []
            current_length = 0

    for block in split_by_paragraphs(text):
        block_parts = (
            [block]
            if len(block) <= max_chars
            else split_long_block(
                block,
                max_chars,
            )
        )

        # 超长结构块已经拆成多个部分。
        # 先提交之前积累的普通结构块。
        if len(block_parts) > 1:
            flush_current()

            for block_part in block_parts:
                if len(block_part) > max_chars:
                    raise RuntimeError(
                        "切片失败，结构块仍然超长："
                        f"{len(block_part):,} 字符"
                    )

                chunks.append(block_part)

            continue

        block_part = block_parts[0]
        added_length = len(block_part)

        if current_blocks:
            added_length += 2

        if (
            current_blocks
            and current_length + added_length
            > max_chars
        ):
            flush_current()

        current_blocks.append(block_part)

        if current_length:
            current_length += 2

        current_length += len(block_part)

    flush_current()

    result = [
        chunk.strip()
        for chunk in chunks
        if chunk.strip()
    ]

    oversized = [
        len(chunk)
        for chunk in result
        if len(chunk) > max_chars
    ]

    if oversized:
        raise RuntimeError(
            "切片失败，仍存在超长分片："
            f"{max(oversized):,} 字符"
        )

    return result


def make_output_dir(
    output_dir: Path,
    relative_path: Path,
) -> Path:
    """
    为每个源文件创建稳定且不会重名的输出目录。
    """
    path_hash = sha256_text(
        relative_path.as_posix()
    )[:10]

    directory_name = (
        f"{safe_name(relative_path.stem)}_"
        f"{path_hash}"
    )

    candidate = (
        output_dir
        / relative_path.parent
        / directory_name
    )

    resolved_output = output_dir.resolve()
    resolved_candidate = candidate.resolve()

    if not resolved_candidate.is_relative_to(
        resolved_output
    ):
        raise RuntimeError(
            f"输出路径超出 output 目录："
            f"{resolved_candidate}"
        )

    return candidate


def get_chunk_paths(
    output_dir: Path,
    source_path: Path,
    part_number: int,
) -> tuple[Path, Path, Path]:
    stem = safe_name(source_path.stem)

    filename = (
        f"{stem}_part_"
        f"{part_number:03d}_cleaned.md"
    )

    output_path = output_dir / filename
    metadata_path = (
        output_dir
        / f"{filename}.meta.json"
    )
    partial_path = (
        output_dir
        / f"{filename}.partial.md"
    )

    return (
        output_path,
        metadata_path,
        partial_path,
    )


def build_expected_metadata(
    relative_path: Path,
    source_sha256: str,
    chunk_sha256: str,
    prompt_sha256: str,
    model: str,
    base_url: str,
    part_number: int,
    total_parts: int,
) -> dict[str, Any]:
    return {
        "source_file": (
            relative_path.as_posix()
        ),
        "source_sha256": source_sha256,
        "chunk_sha256": chunk_sha256,
        "prompt_sha256": prompt_sha256,
        "model": model,
        "base_url": base_url.rstrip("/"),
        "part_number": part_number,
        "total_parts": total_parts,
    }


def build_output_metadata(
    expected: dict[str, Any],
    result: str,
    warnings: list[str],
) -> dict[str, Any]:
    return {
        "version": 1,
        **expected,
        "output_sha256": sha256_text(result),
        "output_chars": len(result),
        "warnings": warnings,
        "completed_at": now_iso(),
    }


def is_completed_chunk(
    output_path: Path,
    metadata_path: Path,
    expected: dict[str, Any],
) -> bool:
    """
    检查输出分片是否真正完成且仍然有效。

    以下任意内容发生变化都会重新处理：

    - 源文件；
    - 分片内容；
    - prompt；
    - 模型；
    - Base URL；
    - 分片数量；
    - 输出文件内容。
    """
    if (
        not output_path.is_file()
        or not metadata_path.is_file()
    ):
        return False

    try:
        result = read_text(
            output_path
        ).strip()

        metadata = json.loads(
            read_text(metadata_path)
        )

        if not result:
            return False

        if not isinstance(metadata, dict):
            return False

        for key, expected_value in (
            expected.items()
        ):
            if (
                metadata.get(key)
                != expected_value
            ):
                return False

        return (
            metadata.get("output_sha256")
            == sha256_text(result)
        )

    except Exception:
        return False


def save_partial_response(
    partial_path: Path,
    text: str,
    reason: str,
) -> None:
    """
    保存流式请求中断时已经收到的部分内容。

    partial 文件不会被识别为正式完成结果。
    """
    if not text.strip():
        return

    safe_reason = (
        reason.replace("--", "—")
        .replace("\r", " ")
        .replace("\n", " ")
    )

    if len(safe_reason) > 500:
        safe_reason = (
            safe_reason[:500] + "……"
        )

    payload = (
        f"<!-- partial saved at "
        f"{now_iso()} -->\n"
        f"<!-- reason: "
        f"{safe_reason} -->\n\n"
        f"{text.rstrip()}\n"
    )

    atomic_write_text(
        partial_path,
        payload,
    )


def build_file_plan(
    source_path: Path,
    input_dir: Path,
    output_dir: Path,
    max_chars: int,
    max_file_size: int,
) -> FilePlan:
    """
    读取一个输入文件并建立处理计划。

    在读取前和读取后分别检查文件大小及路径边界。
    """
    resolved_input = input_dir.resolve()

    resolved_source = ensure_path_inside(
        source_path,
        resolved_input,
        "输入文件",
    )

    if resolved_source.is_symlink():
        raise RuntimeError(
            f"不允许处理符号链接："
            f"{source_path}"
        )

    file_size_before = (
        resolved_source.stat().st_size
    )

    if file_size_before > max_file_size:
        raise RuntimeError(
            f"文件超过大小限制："
            f"{resolved_source.name} "
            f"({file_size_before:,} > "
            f"{max_file_size:,} bytes)"
        )

    relative_path = (
        resolved_source.relative_to(
            resolved_input
        )
    )

    source_text = read_text(
        resolved_source
    )

    file_size_after = (
        resolved_source.stat().st_size
    )

    if file_size_after != file_size_before:
        raise RuntimeError(
            f"读取期间文件发生变化："
            f"{relative_path}"
        )

    chunks = create_chunks(
        source_text,
        max_chars,
    )

    return FilePlan(
        source_path=resolved_source,
        relative_path=relative_path,
        source_sha256=sha256_file(
            resolved_source
        ),
        source_chars=len(source_text),
        chunks=chunks,
        output_dir=make_output_dir(
            output_dir,
            relative_path,
        ),
    )


def build_file_plans(
    source_paths: list[Path],
    input_dir: Path,
    output_dir: Path,
    max_chars: int,
    max_file_size: int,
) -> list[FilePlan]:
    plans: list[FilePlan] = []

    for path in source_paths:
        plans.append(
            build_file_plan(
                source_path=path,
                input_dir=input_dir,
                output_dir=output_dir,
                max_chars=max_chars,
                max_file_size=max_file_size,
            )
        )

    return plans


def plan_has_pending_chunks(
    plan: FilePlan,
    prompt_sha256: str,
    model: str,
    base_url: str,
) -> bool:
    if plan.is_empty:
        return False

    total_parts = len(plan.chunks)

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
            prompt_sha256=prompt_sha256,
            model=model,
            base_url=base_url,
            part_number=part_number,
            total_parts=total_parts,
        )

        if not is_completed_chunk(
            output_path,
            metadata_path,
            expected,
        ):
            return True

    return False
