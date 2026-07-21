from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class QualityThresholds:
    """
    教材清洗质量阈值。

    retained_ratio:
        输出字符数 / 输入字符数。

    expansion_ratio:
        输出字符数 / 输入字符数。

    阈值只用于发现风险，不能证明模型输出正确。
    """

    severe_min_retained_ratio: float
    warning_min_retained_ratio: float
    review_min_retained_ratio: float

    severe_max_expansion_ratio: float
    warning_max_expansion_ratio: float

    heading_retained_ratio: float
    question_retained_ratio: float
    number_retained_ratio: float
    table_retained_ratio: float


def _read_ratio(
    variable_name: str,
    default: float,
) -> float:
    """
    读取 0 到 10 范围内的比例配置。

    允许大于 1 的扩写比例，例如 2.0 表示 200%。
    """
    raw_value = os.getenv(
        variable_name,
        str(default),
    ).strip()

    try:
        value = float(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            f"{variable_name} 必须是数字，"
            f"当前值：{raw_value!r}"
        ) from exc

    if value < 0 or value > 10:
        raise RuntimeError(
            f"{variable_name} 必须位于 0 到 10 之间，"
            f"当前值：{value}"
        )

    return value


def load_quality_thresholds() -> QualityThresholds:
    """
    从环境变量读取质量阈值。

    未配置时使用项目原有默认值。
    """
    thresholds = QualityThresholds(
        severe_min_retained_ratio=_read_ratio(
            "QUALITY_SEVERE_MIN_RETAINED_RATIO",
            0.30,
        ),
        warning_min_retained_ratio=_read_ratio(
            "QUALITY_WARNING_MIN_RETAINED_RATIO",
            0.50,
        ),
        review_min_retained_ratio=_read_ratio(
            "QUALITY_REVIEW_MIN_RETAINED_RATIO",
            0.70,
        ),
        severe_max_expansion_ratio=_read_ratio(
            "QUALITY_SEVERE_MAX_EXPANSION_RATIO",
            2.00,
        ),
        warning_max_expansion_ratio=_read_ratio(
            "QUALITY_WARNING_MAX_EXPANSION_RATIO",
            1.50,
        ),
        heading_retained_ratio=_read_ratio(
            "QUALITY_HEADING_RETAINED_RATIO",
            0.50,
        ),
        question_retained_ratio=_read_ratio(
            "QUALITY_QUESTION_RETAINED_RATIO",
            0.70,
        ),
        number_retained_ratio=_read_ratio(
            "QUALITY_NUMBER_RETAINED_RATIO",
            0.70,
        ),
        table_retained_ratio=_read_ratio(
            "QUALITY_TABLE_RETAINED_RATIO",
            0.50,
        ),
    )

    if not (
        thresholds.severe_min_retained_ratio
        <= thresholds.warning_min_retained_ratio
        <= thresholds.review_min_retained_ratio
    ):
        raise RuntimeError(
            "保留率阈值必须满足："
            "QUALITY_SEVERE_MIN_RETAINED_RATIO "
            "<= QUALITY_WARNING_MIN_RETAINED_RATIO "
            "<= QUALITY_REVIEW_MIN_RETAINED_RATIO"
        )

    if not (
        thresholds.warning_max_expansion_ratio
        <= thresholds.severe_max_expansion_ratio
    ):
        raise RuntimeError(
            "扩写率阈值必须满足："
            "QUALITY_WARNING_MAX_EXPANSION_RATIO "
            "<= QUALITY_SEVERE_MAX_EXPANSION_RATIO"
        )

    return thresholds
