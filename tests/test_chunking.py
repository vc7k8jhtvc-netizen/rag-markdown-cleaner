from __future__ import annotations

import pytest

from clean_auto.chunking import (
    create_chunks,
    is_fenced_code_block,
    split_by_paragraphs,
)


def test_empty_text_returns_no_chunks() -> None:
    assert create_chunks(
        "",
        max_chars=100,
    ) == []


def test_normal_text_respects_limit() -> None:
    text = "安全生产管理教材正文。" * 100

    chunks = create_chunks(
        text,
        max_chars=100,
    )

    assert chunks
    assert all(
        len(chunk) <= 100
        for chunk in chunks
    )

    assert "".join(chunks) == text


def test_paragraph_content_is_preserved() -> None:
    text = (
        "第一段教材正文。\n\n"
        "第二段教材正文。\n\n"
        "第三段教材正文。"
    )

    chunks = create_chunks(
        text,
        max_chars=20,
    )

    normalized_input = text.replace(
        "\n\n",
        "",
    )

    normalized_output = "".join(
        chunks
    ).replace(
        "\n\n",
        "",
    )

    assert normalized_output == normalized_input


def test_oversized_fenced_code_is_rejected() -> None:
    text = (
        "```text\n"
        + "代码内容\n" * 100
        + "```"
    )

    with pytest.raises(
        RuntimeError,
        match="超过单片限制的围栏代码块",
    ):
        create_chunks(
            text,
            max_chars=100,
        )


def test_oversized_markdown_table_is_rejected() -> None:
    text = (
        "| 名称 | 数值 |\n"
        "| --- | --- |\n"
        + "| 项目 | 123 |\n" * 100
    )

    with pytest.raises(
        RuntimeError,
        match="超过单片限制的 Markdown 表格",
    ):
        create_chunks(
            text,
            max_chars=100,
        )


def test_small_fenced_code_is_preserved() -> None:
    text = (
        "```text\n"
        "第一行\n\n"
        "第二行\n"
        "```"
    )

    chunks = create_chunks(
        text,
        max_chars=100,
    )

    assert chunks == [text]


def test_max_chars_must_be_positive() -> None:
    with pytest.raises(
        ValueError,
        match="max_chars 必须大于 0",
    ):
        create_chunks(
            "正文",
            max_chars=0,
        )


def test_indented_code_block_is_preserved_verbatim() -> None:
    """Regression: indentation is Markdown syntax for indented code blocks."""
    text = (
        "    first_call()\n"
        "    second_call()\n"
    )

    chunks = create_chunks(text, max_chars=100)

    assert chunks == [text]
    assert "".join(chunks) == text


def test_indented_code_blank_line_is_preserved_verbatim() -> None:
    """Regression: an indented blank line must stay inside an indented code block."""
    text = (
        "    first_call()\n"
        "    \n"
        "    second_call()\n"
    )

    chunks = create_chunks(text, max_chars=100)

    assert chunks == [text]
    assert "".join(chunks) == text


def test_four_space_backticks_are_not_treated_as_fenced_code() -> None:
    """Regression: four-space backticks are indented code, not a fence opener."""
    text = (
        "    ```\n"
        "    literal backticks\n"
        "    ```\n"
    )

    blocks = split_by_paragraphs(text)
    chunks = create_chunks(text, max_chars=100)

    assert len(blocks) == 1
    assert not is_fenced_code_block(blocks[0])
    assert "".join(chunks) == text


def test_nested_list_indentation_is_preserved() -> None:
    """Regression: a nested list after a paragraph must retain its indentation."""
    text = (
        "Introduction.\n\n"
        "  - Child item\n"
        "    - Grandchild item\n"
        "  - Sibling item\n"
    )

    chunks = create_chunks(text, max_chars=100)

    assert "".join(chunks) == text
    assert "  - Child item\n    - Grandchild item" in chunks[0]


def test_long_list_splits_only_between_complete_items() -> None:
    """Regression: chunk boundaries must not remove list line boundaries or markers."""
    text = (
        "- first list item\n"
        "- second list item\n"
        "- third list item\n"
    )

    chunks = create_chunks(text, max_chars=20)

    assert chunks == [
        "- first list item\n",
        "- second list item\n",
        "- third list item\n",
    ]
    assert "".join(chunks) == text


def test_oversized_list_item_is_not_split_by_character() -> None:
    """Regression: an oversized list item must fail instead of losing its structure."""
    text = (
        "- "
        + "long list item content " * 10
        + "\n  continuation line\n"
    )

    with pytest.raises(RuntimeError):
        create_chunks(text, max_chars=40)


def test_blockquote_prefix_and_indent_are_preserved() -> None:
    """Regression: blockquote prefixes and their container indentation are syntax."""
    text = (
        "Introduction.\n\n"
        "  > quoted line\n"
        "  > continued quote\n"
    )

    chunks = create_chunks(text, max_chars=100)

    assert "".join(chunks) == text
    assert "  > quoted line\n  > continued quote" in chunks[0]


def test_multiple_blank_lines_are_preserved() -> None:
    """Regression: consecutive blank lines are source formatting, not disposable gaps."""
    text = "First paragraph.\n\n\nSecond paragraph.\n\n\n"

    chunks = create_chunks(text, max_chars=100)

    assert chunks == [text]
    assert "".join(chunks) == text


def test_two_space_hard_break_is_preserved() -> None:
    """Regression: two spaces before a newline encode a Markdown hard break."""
    text = "First line with hard break  \n\nSecond paragraph.\n"

    chunks = create_chunks(text, max_chars=100)

    assert "".join(chunks) == text
    assert "hard break  \n" in chunks[0]


def test_chunks_concatenate_to_original_markdown() -> None:
    """Regression: chunking must retain every source character when reassembled."""
    text = (
        "# Heading\n\n"
        "    code line\n"
        "    \n"
        "    second code line\n\n"
        "> Quote with a hard break  \n"
        "> continued quote\n\n\n"
        "- list item\n"
    )

    chunks = create_chunks(text, max_chars=200)

    assert all(len(chunk) <= 200 for chunk in chunks)
    assert "".join(chunks) == text
