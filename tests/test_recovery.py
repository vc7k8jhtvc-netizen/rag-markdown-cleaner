from __future__ import annotations

import pytest

from clean_auto.chunking import create_chunks


def test_empty_text_returns_no_chunks() -> None:
    assert create_chunks(
        "",
        max_chars=100,
    ) == []


def test_normal_text_respects_limit() -> None:
    text = (
        "安全生产管理教材正文。"
        * 100
    )

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
