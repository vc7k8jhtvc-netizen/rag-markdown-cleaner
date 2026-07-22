from __future__ import annotations

from clean_auto.assembly import (
    _remove_front_matter,
)


def test_horizontal_rules_are_not_removed() -> None:
    text = (
        "---\n"
        "必须保留的教材正文\n"
        "---\n\n"
        "后续正文"
    )

    assert _remove_front_matter(text) == text


def test_valid_repeated_front_matter_is_removed() -> None:
    text = (
        "---\n"
        "title: 测试教材\n"
        "subject: 安全生产管理\n"
        "source: null\n"
        "type: 教材\n"
        "year: 2025\n"
        "status: OCR清洗完成\n"
        "---\n\n"
        "后续正文"
    )

    assert _remove_front_matter(text) == (
        "后续正文"
    )


def test_incomplete_front_matter_is_preserved() -> None:
    text = (
        "---\n"
        "title: 测试教材\n"
        "---\n\n"
        "后续正文"
    )

    assert _remove_front_matter(text) == text
