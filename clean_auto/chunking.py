from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    decode_text_bytes,
    FilePlan,
    atomic_write_text,
    now_iso,
    read_text,
    safe_name,
    sha256_file,
    sha256_text,
)


FENCE_START_PATTERN = re.compile(
    r"^(?P<indent> {0,3})(?P<fence>`{3,}|~{3,})"
)


NORMALIZATION_POLICY = "preserve-outer-fence-v1"

SENTENCE_END_PATTERN = re.compile(
    r"[。！？；.!?;][ \t]*"
)

LINE_END_PATTERN = re.compile(
    r"(?:\r\n|\n|\r)$"
)

BLANK_LINE_PATTERN = re.compile(
    r"^[ \t]*(?:\r\n|\n|\r)?$"
)

LIST_ITEM_PATTERN = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?:[-+*]|\d{1,9}[.)])"
    r"(?=[ \t])"
)

BLOCKQUOTE_PATTERN = re.compile(
    r"^ {0,3}>"
)

BLANK_BLOCKQUOTE_PATTERN = re.compile(
    r"^ {0,3}>[ \t]*$"
)

FRONT_MATTER_OPEN_PATTERN = re.compile(
    r"^\ufeff?---[ \t]*$"
)

FRONT_MATTER_CLOSE_PATTERN = re.compile(
    r"^---[ \t]*$"
)


@dataclass(frozen=True)
class _MarkdownBlock:
    text: str
    kind: str


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


def _line_content(line: str) -> str:
    """Return a line without changing any content before its line ending."""
    return LINE_END_PATTERN.sub("", line, count=1)


def _is_blank_line(line: str) -> bool:
    return BLANK_LINE_PATTERN.fullmatch(line) is not None


def _leading_indent_width(line: str) -> int:
    width = 0

    for character in _line_content(line):
        if character == " ":
            width += 1
            continue

        if character == "\t":
            width += 4 - (width % 4)
            continue

        break

    return width


def _is_indented_code_line(line: str) -> bool:
    return (
        not _is_blank_line(line)
        and _leading_indent_width(line) >= 4
    )


def _list_item_indent(line: str) -> int | None:
    match = LIST_ITEM_PATTERN.match(
        _line_content(line)
    )

    if match is None:
        return None

    return _leading_indent_width(
        match.group("indent")
    )


def _is_blockquote_line(line: str) -> bool:
    return BLOCKQUOTE_PATTERN.match(
        _line_content(line)
    ) is not None


def _is_blank_blockquote_line(line: str) -> bool:
    return BLANK_BLOCKQUOTE_PATTERN.fullmatch(
        _line_content(line)
    ) is not None


def _fence_marker(
    line: str,
) -> tuple[str, int] | None:
    """
    返回 CommonMark 围栏的字符和长度。

    围栏最多允许三个前导空格；四个空格时属于缩进代码块。
    """
    match = FENCE_START_PATTERN.match(
        _line_content(line)
    )

    if not match:
        return None

    fence = match.group("fence")

    return fence[0], len(fence)


def _is_fence_close(
    line: str,
    marker_char: str,
    marker_length: int,
) -> bool:
    pattern = re.compile(
        rf"^ {{0,3}}"
        rf"{re.escape(marker_char)}"
        rf"{{{marker_length},}}[ \t]*$"
    )

    return pattern.fullmatch(
        _line_content(line)
    ) is not None


def _consume_fenced_block(
    lines: list[str],
    index: int,
) -> int:
    fence_info = _fence_marker(lines[index])

    if fence_info is None:
        return index + 1

    marker_char, marker_length = fence_info
    index += 1

    while index < len(lines):
        current = lines[index]
        index += 1

        if _is_fence_close(
            current,
            marker_char,
            marker_length,
        ):
            break

    return index


def _consume_indented_code(
    lines: list[str],
    index: int,
) -> int:
    """Include internal blank lines but leave trailing separators outside."""
    cursor = index
    last_code_end = index

    while cursor < len(lines):
        current = lines[cursor]

        if _is_indented_code_line(current):
            cursor += 1
            last_code_end = cursor
            continue

        if _is_blank_line(current):
            cursor += 1
            continue

        break

    return last_code_end


def _consume_list_items(
    lines: list[str],
    index: int,
) -> tuple[list[_MarkdownBlock], int]:
    """Return complete top-level list items without splitting nested content."""
    base_indent = _list_item_indent(lines[index])

    if base_indent is None:
        return [], index + 1

    blocks: list[_MarkdownBlock] = []
    item_start = index
    cursor = index + 1

    while cursor < len(lines):
        current = lines[cursor]

        if _is_blank_line(current):
            blank_start = cursor

            while (
                cursor < len(lines)
                and _is_blank_line(lines[cursor])
            ):
                cursor += 1

            if cursor >= len(lines):
                blocks.append(
                    _MarkdownBlock(
                        text="".join(
                            lines[item_start:blank_start]
                        ),
                        kind="list_item",
                    )
                )
                return blocks, blank_start

            next_indent = _list_item_indent(
                lines[cursor]
            )

            if (
                next_indent is not None
                and next_indent > base_indent
            ):
                cursor += 1
                continue

            if (
                next_indent is None
                and _leading_indent_width(
                    lines[cursor]
                ) > base_indent
            ):
                cursor += 1
                continue

            blocks.append(
                _MarkdownBlock(
                    text="".join(
                        lines[item_start:blank_start]
                    ),
                    kind="list_item",
                )
            )
            return blocks, blank_start

        next_indent = _list_item_indent(current)

        if next_indent == base_indent:
            blocks.append(
                _MarkdownBlock(
                    text="".join(
                        lines[item_start:cursor]
                    ),
                    kind="list_item",
                )
            )
            item_start = cursor
            cursor += 1
            continue

        if (
            next_indent is not None
            and next_indent < base_indent
        ):
            break

        cursor += 1

    blocks.append(
        _MarkdownBlock(
            text="".join(lines[item_start:cursor]),
            kind="list_item",
        )
    )

    return blocks, cursor


def _is_structural_start(line: str) -> bool:
    list_indent = _list_item_indent(line)

    return (
        _fence_marker(line) is not None
        or (
            list_indent is not None
            and list_indent <= 3
        )
        or _is_blockquote_line(line)
    )


def _consume_blockquote_paragraphs(
    lines: list[str],
    index: int,
) -> tuple[list[_MarkdownBlock], int]:
    """Split a quote only after an explicit quoted blank line."""
    blocks: list[_MarkdownBlock] = []
    paragraph_start = index
    cursor = index

    while cursor < len(lines):
        current = lines[cursor]

        if _is_blank_line(current):
            break

        if _is_blockquote_line(current):
            cursor += 1

            if _is_blank_blockquote_line(current):
                blocks.append(
                    _MarkdownBlock(
                        text="".join(
                            lines[paragraph_start:cursor]
                        ),
                        kind="blockquote",
                    )
                )
                paragraph_start = cursor

            continue

        if _is_structural_start(current):
            break

        # CommonMark permits lazy continuation lines in a blockquote paragraph.
        cursor += 1

    if paragraph_start < cursor:
        blocks.append(
            _MarkdownBlock(
                text="".join(
                    lines[paragraph_start:cursor]
                ),
                kind="blockquote",
            )
        )

    return blocks, cursor


def _scan_markdown_blocks(
    text: str,
) -> list[_MarkdownBlock]:
    """Scan Markdown into exact source spans whose concatenation is unchanged."""
    lines = text.splitlines(keepends=True)
    blocks: list[_MarkdownBlock] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        if _is_blank_line(line):
            start = index

            while (
                index < len(lines)
                and _is_blank_line(lines[index])
            ):
                index += 1

            blocks.append(
                _MarkdownBlock(
                    text="".join(lines[start:index]),
                    kind="blank",
                )
            )
            continue

        if (
            index == 0
            and FRONT_MATTER_OPEN_PATTERN.fullmatch(
                _line_content(line)
            )
            is not None
        ):
            start = index
            index += 1

            while index < len(lines):
                current = lines[index]
                index += 1

                if FRONT_MATTER_CLOSE_PATTERN.fullmatch(
                    _line_content(current)
                ) is not None:
                    break

            blocks.append(
                _MarkdownBlock(
                    text="".join(lines[start:index]),
                    kind="front_matter",
                )
            )
            continue

        if _fence_marker(line) is not None:
            start = index
            index = _consume_fenced_block(
                lines,
                index,
            )
            blocks.append(
                _MarkdownBlock(
                    text="".join(lines[start:index]),
                    kind="fenced_code",
                )
            )
            continue

        if _is_indented_code_line(line):
            start = index
            index = _consume_indented_code(
                lines,
                index,
            )
            blocks.append(
                _MarkdownBlock(
                    text="".join(lines[start:index]),
                    kind="indented_code",
                )
            )
            continue

        list_indent = _list_item_indent(line)

        if (
            list_indent is not None
            and list_indent <= 3
        ):
            list_blocks, index = (
                _consume_list_items(
                    lines,
                    index,
                )
            )
            blocks.extend(list_blocks)
            continue

        if _is_blockquote_line(line):
            quote_blocks, index = (
                _consume_blockquote_paragraphs(
                    lines,
                    index,
                )
            )
            blocks.extend(quote_blocks)
            continue

        start = index
        index += 1

        while index < len(lines):
            current = lines[index]

            if (
                _is_blank_line(current)
                or _is_structural_start(current)
            ):
                break

            index += 1

        block_text = "".join(lines[start:index])
        block_kind = (
            "table"
            if is_markdown_table_block(block_text)
            else "normal"
        )
        blocks.append(
            _MarkdownBlock(
                text=block_text,
                kind=block_kind,
            )
        )

    return blocks


def split_by_paragraphs(
    text: str,
) -> list[str]:
    """Return exact Markdown structure spans without normalizing source text."""
    return [
        block.text
        for block in _scan_markdown_blocks(text)
    ]


def _find_preferred_split(
    text: str,
    max_chars: int,
) -> int:
    """
    在最大字符限制内寻找自然切分位置。

    优先顺序：

    1. 完整换行边界；
    2. 中文或英文句末标点；
    3. 不属于硬换行的空格或制表符。

    为避免产生特别短的分片，只接受位于窗口后半段的
    自然边界。没有安全边界时返回 0，由调用方明确拒绝切分。
    """
    if len(text) <= max_chars:
        return len(text)

    window = text[:max_chars]
    minimum_position = max(
        1,
        int(max_chars * 0.5),
    )

    line_positions = [
        match.end()
        for match in re.finditer(
            r"\r\n|\n|\r",
            window,
        )
        if match.end() >= minimum_position
    ]

    if line_positions:
        return line_positions[-1]

    def separates_line_ending(
        position: int,
    ) -> bool:
        return re.match(
            r"[ \t]*(?:\r\n|\n|\r)",
            text[position:],
        ) is not None

    sentence_positions = [
        match.end()
        for match in SENTENCE_END_PATTERN.finditer(
            window
        )
        if (
            match.end() >= minimum_position
            and not separates_line_ending(
                match.end()
            )
        )
    ]

    if sentence_positions:
        return sentence_positions[-1]

    whitespace_positions = [
        index + 1
        for index, character in enumerate(window)
        if (
            character in {" ", "\t"}
            and index + 1 >= minimum_position
            and not separates_line_ending(
                index + 1
            )
        )
    ]

    if whitespace_positions:
        return whitespace_positions[-1]

    return 0


def split_line_by_chars(
    line: str,
    max_chars: int,
) -> list[str]:
    """
    拆分超过限制的单行文本。

    只在完整换行、句末或安全空白边界切分；
    没有安全边界时拒绝按字符破坏 Markdown。
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
            raise RuntimeError(
                "无法在不破坏 Markdown 结构的情况下拆分超长普通文本："
                f"{len(remaining):,} > {max_chars:,} 字符"
            )

        part = remaining[:split_position]
        remaining = remaining[split_position:]

        if part:
            parts.append(part)

    if remaining:
        parts.append(remaining)

    return parts


def is_fenced_code_block(
    block: str,
) -> bool:
    """
    判断结构块是否为 Markdown 围栏代码块。
    """
    lines = block.splitlines()

    if not lines:
        return False

    return _fence_marker(lines[0]) is not None


def is_markdown_table_block(
    block: str,
) -> bool:
    """
    判断结构块是否包含 Markdown 表格分隔行。

    典型分隔行：

    | --- | --- |
    | :--- | ---: |
    """
    separator_pattern = re.compile(
        r"^[ \t]*\|?"
        r"[ \t]*:?-{3,}:?[ \t]*"
        r"(?:\|[ \t]*:?-{3,}:?[ \t]*)+"
        r"\|?[ \t]*$"
    )

    return any(
        separator_pattern.fullmatch(line)
        for line in block.splitlines()
    )


def _raise_unsafe_block_error(
    block: _MarkdownBlock,
    max_chars: int,
) -> None:
    if block.kind == "fenced_code":
        raise RuntimeError(
            "检测到超过单片限制的围栏代码块："
            f"{len(block.text):,} > {max_chars:,} 字符。"
            "为避免破坏代码围栏，程序已停止切分。"
            "请增大 --max-chars，或人工拆分该代码块。"
        )

    if block.kind == "indented_code":
        raise RuntimeError(
            "检测到超过单片限制的缩进代码块："
            f"{len(block.text):,} > {max_chars:,} 字符。"
            "为避免破坏代码缩进，程序已停止切分。"
            "请增大 --max-chars，或人工拆分该代码块。"
        )

    if block.kind == "table":
        raise RuntimeError(
            "检测到超过单片限制的 Markdown 表格："
            f"{len(block.text):,} > {max_chars:,} 字符。"
            "为避免丢失表头或破坏表格结构，"
            "程序已停止切分。"
            "请增大 --max-chars，或人工拆分该表格。"
        )

    if block.kind == "list_item":
        raise RuntimeError(
            "检测到超过单片限制的 Markdown 列表项："
            f"{len(block.text):,} > {max_chars:,} 字符。"
            "列表只能在完整列表项之间切分。"
            "请增大 --max-chars，或人工拆分该列表项。"
        )

    if block.kind == "blockquote":
        raise RuntimeError(
            "检测到超过单片限制的 Markdown 引用段落："
            f"{len(block.text):,} > {max_chars:,} 字符。"
            "引用只能在完整引用段落之间切分。"
            "请增大 --max-chars，或人工拆分该引用段落。"
        )

    if block.kind == "front_matter":
        raise RuntimeError(
            "检测到超过单片限制的 YAML Front Matter："
            f"{len(block.text):,} > {max_chars:,} 字符。"
            "为避免破坏元数据，程序已停止切分。"
        )

    raise RuntimeError(
        "无法安全拆分 Markdown 结构块："
        f"{len(block.text):,} > {max_chars:,} 字符"
    )


def _split_markdown_block(
    block: _MarkdownBlock,
    max_chars: int,
) -> list[str]:
    if len(block.text) <= max_chars:
        return [block.text]

    if block.kind in {
        "fenced_code",
        "indented_code",
        "table",
        "list_item",
        "blockquote",
        "front_matter",
    }:
        _raise_unsafe_block_error(
            block,
            max_chars,
        )

    return split_line_by_chars(
        block.text,
        max_chars,
    )


def _pack_markdown_blocks(
    blocks: list[_MarkdownBlock],
    max_chars: int,
) -> list[str]:
    chunks: list[str] = []
    current = ""

    for block in blocks:
        block_parts = _split_markdown_block(
            block,
            max_chars,
        )

        if len(block_parts) > 1:
            if current:
                chunks.append(current)
                current = ""

            chunks.extend(block_parts)
            continue

        block_part = block_parts[0]

        if (
            current
            and len(current) + len(block_part)
            > max_chars
        ):
            chunks.append(current)
            current = ""

        if len(block_part) > max_chars:
            raise RuntimeError(
                "切片失败，结构块仍然超长："
                f"{len(block_part):,} 字符"
            )

        current += block_part

    if current:
        chunks.append(current)

    return chunks


def split_long_block(
    block: str,
    max_chars: int,
) -> list[str]:
    """Split an exact Markdown source span only at safe structure boundaries."""
    if max_chars <= 0:
        raise ValueError(
            "max_chars 必须大于 0"
        )

    return _pack_markdown_blocks(
        _scan_markdown_blocks(block),
        max_chars,
    )


def create_chunks(
    text: str,
    max_chars: int,
) -> list[str]:
    """
    创建发送给模型的 Markdown 分片。

    每个分片都是源文本的连续区间；所有分片连接后必须与源文本完全一致。
    代码块、表格、列表项和引用段落只在安全结构边界切分。
    """
    if max_chars <= 0:
        raise ValueError(
            "max_chars 必须大于 0"
        )

    if not text:
        return []

    result = _pack_markdown_blocks(
        _scan_markdown_blocks(text),
        max_chars,
    )

    if "".join(result) != text:
        raise RuntimeError(
            "切片失败：分片无法无损还原源 Markdown"
        )

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
    strict_validation: bool = False,
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
        "strict_validation": strict_validation,
        "normalization_policy": NORMALIZATION_POLICY,
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

    raw_source = resolved_source.read_bytes()
    source_text = decode_text_bytes(
        raw_source,
        resolved_source,
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

    source_sha256 = hashlib.sha256(raw_source).hexdigest()
    if sha256_file(resolved_source) != source_sha256:
        raise RuntimeError(
            f"文件发生变化，拒绝使用旧计划：{relative_path}"
        )

    return FilePlan(
        source_path=resolved_source,
        relative_path=relative_path,
        source_sha256=source_sha256,
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
    strict_validation: bool = False,
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
            strict_validation=strict_validation,
        )

        if not is_completed_chunk(
            output_path,
            metadata_path,
            expected,
        ):
            return True

    return False
