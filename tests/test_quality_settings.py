from __future__ import annotations

import pytest

from clean_auto.quality import (
    assess_quality,
    find_added_urls,
)
from clean_auto.quality_settings import (
    clear_quality_threshold_cache,
    load_quality_thresholds,
)


QUALITY_ENVIRONMENT_VARIABLES = (
    "QUALITY_SEVERE_MIN_RETAINED_RATIO",
    "QUALITY_WARNING_MIN_RETAINED_RATIO",
    "QUALITY_REVIEW_MIN_RETAINED_RATIO",
    "QUALITY_SEVERE_MAX_EXPANSION_RATIO",
    "QUALITY_WARNING_MAX_EXPANSION_RATIO",
    "QUALITY_HEADING_RETAINED_RATIO",
    "QUALITY_QUESTION_RETAINED_RATIO",
    "QUALITY_NUMBER_RETAINED_RATIO",
    "QUALITY_TABLE_RETAINED_RATIO",
)


@pytest.fixture(autouse=True)
def reset_quality_settings_cache(
    monkeypatch,
):
    """
    每个测试前后清除环境变量和阈值缓存。

    正式程序会缓存一次，但测试必须保持相互独立。
    """
    for variable_name in (
        QUALITY_ENVIRONMENT_VARIABLES
    ):
        monkeypatch.delenv(
            variable_name,
            raising=False,
        )

    clear_quality_threshold_cache()

    yield

    clear_quality_threshold_cache()


def test_default_thresholds_match_project_defaults() -> None:
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


def test_quality_thresholds_are_cached(
    monkeypatch,
) -> None:
    """
    首次读取后，即使环境变量变化，本进程仍使用同一配置。

    这保证大批处理不会为每个分片重复解析环境变量。
    """
    monkeypatch.setenv(
        "QUALITY_SEVERE_MIN_RETAINED_RATIO",
        "0.20",
    )

    first = load_quality_thresholds()

    monkeypatch.setenv(
        "QUALITY_SEVERE_MIN_RETAINED_RATIO",
        "0.10",
    )

    second = load_quality_thresholds()

    assert first is second

    assert (
        second.severe_min_retained_ratio
        == 0.20
    )


def test_cache_can_be_explicitly_cleared(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "QUALITY_SEVERE_MIN_RETAINED_RATIO",
        "0.20",
    )

    first = load_quality_thresholds()

    monkeypatch.setenv(
        "QUALITY_SEVERE_MIN_RETAINED_RATIO",
        "0.10",
    )

    clear_quality_threshold_cache()

    second = load_quality_thresholds()

    assert first is not second

    assert (
        second.severe_min_retained_ratio
        == 0.10
    )


def test_custom_retention_threshold_is_used(
    monkeypatch,
) -> None:
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

    clear_quality_threshold_cache()

    quality = assess_quality(
        input_text="教材正文。" * 100,
        output_text="清洗结果。" * 20,
    )

    assert quality.severe_errors == []
    assert quality.review_required


def test_invalid_retention_order_is_rejected(
    monkeypatch,
) -> None:
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

    clear_quality_threshold_cache()

    with pytest.raises(
        RuntimeError,
        match="保留率阈值必须满足",
    ):
        load_quality_thresholds()


def test_invalid_expansion_order_is_rejected(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "QUALITY_WARNING_MAX_EXPANSION_RATIO",
        "3.00",
    )

    monkeypatch.setenv(
        "QUALITY_SEVERE_MAX_EXPANSION_RATIO",
        "2.00",
    )

    clear_quality_threshold_cache()

    with pytest.raises(
        RuntimeError,
        match="扩写率阈值必须满足",
    ):
        load_quality_thresholds()


def test_non_numeric_threshold_is_rejected(
    monkeypatch,
) -> None:
    monkeypatch.setenv(
        "QUALITY_NUMBER_RETAINED_RATIO",
        "not-a-number",
    )

    clear_quality_threshold_cache()

    with pytest.raises(
        RuntimeError,
        match="必须是数字",
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
        "教材正文："
        "https://example.com/original"
    )

    output_text = (
        "教材正文："
        "https://example.com/original\n"
        "新增链接："
        "https://example.com/new"
    )

    assert find_added_urls(
        input_text,
        output_text,
    ) == [
        "https://example.com/new"
    ]
