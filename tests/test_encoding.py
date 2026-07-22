from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from clean_auto.chunking import build_file_plan, create_chunks
from clean_auto.config import (
    atomic_write_text,
    read_text,
    sha256_text,
    strip_outer_code_fence,
)


def _prompt_hash(path: Path) -> str:
    return sha256_text(
        strip_outer_code_fence(
            read_text(path)
        )
    )


@pytest.mark.parametrize(
    ("raw_content", "expected_text"),
    [
        (b"first\r\nsecond\r\n", "first\r\nsecond\r\n"),
        (b"first\nsecond\n", "first\nsecond\n"),
        (
            b"first\r\nsecond\nthird\rfourth\r\n",
            "first\r\nsecond\nthird\rfourth\r\n",
        ),
    ],
    ids=["crlf", "lf", "mixed"],
)
def test_read_text_preserves_line_endings(
    tmp_path: Path,
    raw_content: bytes,
    expected_text: str,
) -> None:
    source_path = tmp_path / "source.md"
    source_path.write_bytes(raw_content)

    assert read_text(source_path) == expected_text


def test_read_text_decodes_utf8_without_bom(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "utf8.md"
    source_text = "中文 English cafe\u0301 and emoji \U0001f680\n"
    source_path.write_bytes(
        source_text.encode("utf-8")
    )

    assert read_text(source_path) == source_text


def test_read_text_removes_utf8_bom(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "utf8-bom.md"
    source_text = "中文 English cafe\u0301\n"
    source_path.write_bytes(
        b"\xef\xbb\xbf" + source_text.encode("utf-8")
    )

    result = read_text(source_path)

    assert result == source_text
    assert not result.startswith("\ufeff")


def test_read_text_removes_bom_and_preserves_crlf(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "utf8-bom-crlf.md"
    source_text = "标题\r\n\r\n正文 English\r\n"
    source_path.write_bytes(
        b"\xef\xbb\xbf" + source_text.encode("utf-8")
    )

    assert read_text(source_path) == source_text


def test_read_text_decodes_gb18030_only_character(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "gb18030.md"
    source_text = "GB18030 扩展字符：\U00020000\r\n"
    source_path.write_bytes(
        source_text.encode("gb18030")
    )

    assert read_text(source_path) == source_text


def test_read_text_decodes_gbk_compatible_bytes(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "gbk.md"
    source_text = "GBK 中文内容\r\n"
    source_path.write_bytes(
        source_text.encode("gbk")
    )

    assert read_text(source_path) == source_text


def test_read_text_raises_runtime_error_after_all_codecs_fail(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "invalid.md"
    source_path.write_bytes(b"\xff")

    with pytest.raises(RuntimeError) as exc_info:
        read_text(source_path)

    assert isinstance(
        exc_info.value.__cause__,
        UnicodeDecodeError,
    )


def test_read_text_propagates_file_not_found_error(
    tmp_path: Path,
) -> None:
    with pytest.raises(FileNotFoundError):
        read_text(tmp_path / "missing.md")


def test_crlf_text_is_preserved_after_chunking(
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "source.md"
    source_path.write_bytes(
        b"First paragraph text.\r\n\r\n"
        b"Second paragraph text.\r\n\r\n"
        b"Third paragraph text.\r\n"
    )
    source_text = read_text(source_path)

    chunks = create_chunks(source_text, max_chars=30)

    assert len(chunks) > 1
    assert "".join(chunks) == source_text
    assert "\r\n" in "".join(chunks)


def test_build_file_plan_preserves_crlf_and_hashes_raw_bytes(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    output_dir = tmp_path / "output"
    source_path = input_dir / "source.md"
    source_text = (
        "# 标题\r\n\r\n"
        "第一段中文与 English。\r\n\r\n"
        "第二段保留 CRLF。\r\n"
    )
    raw_content = source_text.encode("utf-8")
    input_dir.mkdir()
    source_path.write_bytes(raw_content)

    plan = build_file_plan(
        source_path=source_path,
        input_dir=input_dir,
        output_dir=output_dir,
        max_chars=30,
        max_file_size=100_000,
    )

    assert plan.source_chars == len(source_text)
    assert "".join(plan.chunks) == source_text
    assert plan.source_sha256 == hashlib.sha256(
        raw_content
    ).hexdigest()

    source_path.write_bytes(
        source_text.replace("\r\n", "\n").encode("utf-8")
    )
    lf_plan = build_file_plan(
        source_path=source_path,
        input_dir=input_dir,
        output_dir=output_dir,
        max_chars=30,
        max_file_size=100_000,
    )

    assert lf_plan.source_sha256 != plan.source_sha256


def test_prompt_hash_changes_for_internal_crlf(
    tmp_path: Path,
) -> None:
    lf_prompt = tmp_path / "prompt-lf.md"
    crlf_prompt = tmp_path / "prompt-crlf.md"
    lf_prompt.write_bytes(
        b"Keep all content.\nPreserve Markdown."
    )
    crlf_prompt.write_bytes(
        b"Keep all content.\r\nPreserve Markdown."
    )

    assert _prompt_hash(lf_prompt) != _prompt_hash(crlf_prompt)


def test_prompt_bom_does_not_change_decoded_text_or_hash(
    tmp_path: Path,
) -> None:
    prompt_text = "Keep all content.\r\nPreserve Markdown."
    plain_prompt = tmp_path / "prompt.md"
    bom_prompt = tmp_path / "prompt-bom.md"
    plain_prompt.write_bytes(
        prompt_text.encode("utf-8")
    )
    bom_prompt.write_bytes(
        b"\xef\xbb\xbf" + prompt_text.encode("utf-8")
    )

    assert read_text(bom_prompt) == read_text(plain_prompt)
    assert _prompt_hash(bom_prompt) == _prompt_hash(plain_prompt)


def test_write_read_round_trip_preserves_line_endings(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "round-trip.md"
    source_text = "first\r\nsecond\nthird\rfourth\r\n"

    atomic_write_text(output_path, source_text)

    assert output_path.read_bytes() == source_text.encode("utf-8")
    assert read_text(output_path) == source_text


def test_atomic_write_text_writes_utf8_without_adding_bom(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "utf8.md"
    source_text = "中文\r\nEnglish cafe\u0301\r\n"

    atomic_write_text(output_path, source_text)

    raw_content = output_path.read_bytes()

    assert raw_content == source_text.encode("utf-8")
    assert raw_content.decode("utf-8") == source_text
    assert not raw_content.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" in raw_content


def test_atomic_write_text_preserves_a_passed_bom_character(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "explicit-bom.md"
    source_text = "\ufeff中文\r\n"

    atomic_write_text(output_path, source_text)

    assert output_path.read_bytes() == source_text.encode("utf-8")
    assert output_path.read_bytes().startswith(b"\xef\xbb\xbf")
