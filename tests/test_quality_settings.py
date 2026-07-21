from __future__ import annotations

import pytest

from clean_auto.quality import (
    assess_quality,
    find_added_urls,
)
from clean_auto.quality_settings import (
    load_quality_thresholds,
)


def clear_quality_environment(
    monkeypatch,
) -> None:
    for variable_name in (
        "QUALITY_SEVERE_MIN_RETAINED_RATIO",
        "QUALITY_WARNING_MIN_RETAINED_RATIO",
        "QUALITY_REVIEW_MIN_RETAINED_RATIO",
        "QUALITY_SEVERE_MAX_EXPANSION_RATIO",
        "QUALITY_WARNING_MAX_EXPANSION_RATIO",
        "QUALITY_HEADING_RETAINED_RATIO",
        "QUALITY_QUESTION_RETAINED_RATIO",
        "QUALITY_NUMBER_RETAINED_RATIO",
        "QUALITY_TABLE_RETAINED_RATIO",
    ):
        monkeypatch.delenv(
            variable_name,
            raising=False,
        )


def test_default_thresholds_match_project_defaults(
    monkeypatch,
) -> None:
    clear_quality_environment(
        monkeypatch
    )

    thresholds = load_quality_thresholds()

    assert (
        thresholds.severe_min_retained_ratio
        == 0.30
    )
    assert (
        thresholds.warning_min_retained_ratio
        == 0.50
    )
    assert (
        thresholds.review_min_retained_ratio
        == 0.70
    )
    assert (
        thresholds.severe_max_expansion_ratio
        == 2.00
    )
    assert (
        thresholds.warning_max_expansion_ratio
        == 1.50
    )


def test_custom_retention_threshold_is_used(
    monkeypatch,
) -> None:
    clear_quality_environment(
        monkeypatch
    )

    monkeypatch.setenv(
        "QUALITY_SEVERE_MIN_RETAINED_RATIO",
        "0.10",
    )
    monkeypatch.setenv(
        "QUALITY_WARNING_MIN_RETAINED_RATIO",
        "0.20",
    )
    monkeypatch.setenv(
        "QUALITY_REVIEW_MIN_RETAINED_RATIO",
        "0.30",
    )

    quality = assess_quality(
        input_text="教材正文。" * 100,
        output_text="清洗结果。" * 20,
    )

    assert quality.severe_errors == []
    assert quality.review_required


def test_invalid_retention_order_is_rejected(
    monkeypatch,
) -> None:
    clear_quality_environment(
        monkeypatch
    )

    monkeypatch.setenv(
        "QUALITY_SEVERE_MIN_RETAINED_RATIO",
        "0.60",
    )
    monkeypatch.setenv(
        "QUALITY_WARNING_MIN_RETAINED_RATIO",
        "0.50",
    )
    monkeypatch.setenv(
        "QUALITY_REVIEW_MIN_RETAINED_RATIO",
        "0.70",
    )

    with pytest.raises(
        RuntimeError,
        match="保留率阈值必须满足",
    ):
        load_quality_thresholds()


def test_url_punctuation_change_is_not_added_url() -> None:
    input_text = (
        "官方地址：https://example.com/path。"
    )
    output_text = (
        "官方地址：https://example.com/path"
    )

    assert find_added_urls(
        input_text,
        output_text,
    ) == []


def test_real_new_url_is_detected() -> None:
    input_text = (
        "教材正文：https://example.com/original"
    )
    output_text = (
        "教材正文：https://example.com/original\n"
        "新增链接：https://example.com/new"
    )

    assert find_added_urls(
        input_text,
        output_text,
    ) == [
        "https://example.com/new"
    ]
