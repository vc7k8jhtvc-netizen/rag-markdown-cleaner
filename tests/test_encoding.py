from __future__ import annotations

from pathlib import Path

import pytest

from clean_auto.chunking import create_chunks
from clean_auto.config import atomic_write_text, read_text


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


def test_write_read_round_trip_preserves_line_endings(
    tmp_path: Path,
) -> None:
    output_path = tmp_path / "round-trip.md"
    source_text = "first\r\nsecond\nthird\rfourth\r\n"

    atomic_write_text(output_path, source_text)

    assert output_path.read_bytes() == source_text.encode("utf-8")
    assert read_text(output_path) == source_text
