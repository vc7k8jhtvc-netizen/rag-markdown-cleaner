from __future__ import annotations

import pytest

from clean_auto.quality import assess_quality
from clean_auto.validation import remove_outer_code_fence, validate_result


@pytest.mark.parametrize(
    "text",
    [
        "```text\nverbatim text\n```",
        "```markdown\n# Heading\n```",
        "```\nplain fenced content\n```",
        "\n```text\n  meaningful spacing  \n```\n",
    ],
)
def test_outer_code_fence_is_preserved_losslessly(text: str) -> None:
    assert remove_outer_code_fence(text) == text


def valid_first_part() -> str:
    return (
        "---\n"
        "title: 测试教材\n"
        "subject: 安全生产管理\n"
        "source: null\n"
        "type: 教材\n"
        "year: 2025\n"
        "status: OCR清洗完成\n"
        "---\n\n"
        "# 第一章\n\n"
        "这是中级注册安全工程师教材正文。"
    )


def test_valid_first_part_passes_strict_validation() -> None:
    result = valid_first_part()

    errors, warnings = validate_result(
        result=result,
        input_chunk=result,
        strict_validation=True,
        part_number=1,
    )

    assert errors == []
    assert warnings == []


def test_missing_front_matter_fails_in_strict_mode() -> None:
    errors, _ = validate_result(
        result="# 第一章\n\n教材正文。",
        input_chunk="# 第一章\n\n教材正文。",
        strict_validation=True,
        part_number=1,
    )

    assert any(
        "Front Matter" in error
        for error in errors
    )


def test_missing_front_matter_is_warning_in_non_strict_mode() -> None:
    errors, warnings = validate_result(
        result="# 第一章\n\n教材正文。",
        input_chunk="# 第一章\n\n教材正文。",
        strict_validation=False,
        part_number=1,
    )

    assert errors == []
    assert any(
        "Front Matter" in warning
        for warning in warnings
    )


def test_html_error_page_is_rejected() -> None:
    result = (
        "<!doctype html>\n"
        "<html><head><title>502 Bad Gateway</title>"
        "</head><body>Service Unavailable</body></html>"
    )

    errors, _ = validate_result(
        result=result,
        input_chunk="教材正文。",
        strict_validation=False,
        part_number=1,
    )

    assert any(
        "HTML" in error
        for error in errors
    )


def test_later_part_without_front_matter_is_allowed() -> None:
    result = (
        "第二节 安全生产责任制\n\n"
        "本节教材正文。"
    )

    errors, warnings = validate_result(
        result=result,
        input_chunk=result,
        strict_validation=True,
        part_number=2,
    )

    assert errors == []
    assert warnings == []


def test_quality_rejects_severe_content_loss() -> None:
    input_text = "安全生产教材正文。" * 100
    output_text = "清洗后内容。"

    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert quality.severe_errors
    assert quality.review_required


def test_quality_warns_about_added_url() -> None:
    input_text = (
        "# 第一章\n\n"
        "安全生产管理教材正文。"
    )

    output_text = (
        "# 第一章\n\n"
        "安全生产管理教材正文。\n\n"
        "https://example.com/new-link"
    )

    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert quality.added_urls == [
        "https://example.com/new-link"
    ]
    assert quality.review_required
    assert any(
        "URL" in warning
        for warning in quality.warnings
    )


def test_quality_accepts_similar_normal_content() -> None:
    input_text = (
        "# 第一章\n\n"
        "安全生产管理是安全生产工作的基础。\n\n"
        "生产经营单位应当建立安全生产责任制。"
    )

    output_text = (
        "# 第一章\n\n"
        "安全生产管理是安全生产工作的基础。\n\n"
        "生产经营单位应当建立安全生产责任制。"
    )

    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert quality.severe_errors == []
    assert quality.review_required is False


def test_quality_rejects_same_count_replaced_numbers() -> None:
    input_text = "\n".join(
        f"第{value}条规定数值{value}。"
        for value in range(1, 31)
    )
    output_text = "\n".join(
        f"第{value}条规定数值{value}。"
        for value in range(101, 131)
    )

    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert quality.input_numbers == quality.output_numbers
    assert quality.protected_values_changed
    assert quality.severe_errors
    assert quality.review_required


def test_quality_rejects_similar_length_body_replacement() -> None:
    input_text = (
        "安全生产责任制要求生产经营单位明确岗位职责，"
        "定期检查风险并记录整改结果。"
    ) * 12
    output_text = (
        "城市园林绿化工作需要合理选择植物品种，"
        "持续观察生长情况并安排日常养护。"
    ) * 12

    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert quality.protected_anchor_ratio < 0.35
    assert any(
        "大范围替换" in error
        for error in quality.severe_errors
    )
    assert quality.review_required


def test_quality_allows_protected_values_in_removed_ad_line() -> None:
    input_text = (
        "安全生产教材正文中的限值为 30%。\n"
        "扫码关注领取 2025 年课程资料。"
    )
    output_text = "安全生产教材正文中的限值为 30%。"

    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert not quality.protected_values_changed
    assert quality.severe_errors == []


@pytest.mark.parametrize(
    ("input_text", "output_text"),
    [
        ("允许浓度为 30 mg/m³。", "允许浓度为 30 g/m³。"),
        ("执行标准 GB/T 33000-2016。", "执行标准 AQ/T 33000-2016。"),
    ],
)
def test_quality_rejects_changed_unit_or_standard_id(
    input_text: str,
    output_text: str,
) -> None:
    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert quality.protected_values_changed
    assert quality.severe_errors
    assert quality.review_required


def test_quality_allows_new_first_part_front_matter() -> None:
    input_text = "# 第一章\n\n教材正文中的限值为 30%。"
    output_text = (
        "---\n"
        "title: 测试教材\n"
        "year: 2025\n"
        "---\n\n"
        "# 第一章\n\n"
        "教材正文中的限值为 30%。"
    )

    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert not quality.protected_values_changed
    assert quality.severe_errors == []


def test_quality_protects_existing_front_matter_values() -> None:
    input_text = "---\nyear: 2025\n---\n\n教材正文。"
    output_text = "---\nyear: 2026\n---\n\n教材正文。"

    quality = assess_quality(
        input_text=input_text,
        output_text=output_text,
    )

    assert quality.protected_values_changed
    assert quality.severe_errors
