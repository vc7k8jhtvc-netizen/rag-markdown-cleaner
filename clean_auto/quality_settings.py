from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class QualityThresholds:
    """
    教材清洗质量阈值。

    retained_ratio:
        输出字符数 / 输入字符数。

    expansion_ratio:
        输出字符数 / 输入字符数。

    阈值只用于发现风险，不能证明模型输出完全正确。
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
    读取比例环境变量。

    允许的范围是 0 到 10。

    扩写比例可以大于 1，例如：

    1.5 = 150%
    2.0 = 200%
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


@lru_cache(maxsize=1)
def load_quality_thresholds() -> QualityThresholds:
    """
    从环境变量加载质量阈值。

    每个 Python 进程只加载一次。

    正常启动顺序是：

    1. config.py 加载项目根目录的 .env；
    2. pipeline 建立运行配置；
    3. processor 调用 assess_quality()；
    4. 本函数首次加载并缓存阈值。

    因此，一个大批次即使处理数千个分片，也不会反复执行
    os.getenv、float 转换和阈值合法性检查。

    如果运行过程中修改 .env，需要重启程序后才会生效。
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


def clear_quality_threshold_cache() -> None:
    """
    清除质量阈值缓存。

    正式程序通常不需要调用本函数。

    它主要用于：

    - 自动化测试；
    - 交互式调试；
    - 同一个 Python 进程中主动修改环境变量后的重新加载。

    普通用户修改 .env 后，重新启动程序即可。
    """
    load_quality_thresholds.cache_clear()
