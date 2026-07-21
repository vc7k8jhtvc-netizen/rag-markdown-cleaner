from __future__ import annotations

import re
from dataclasses import asdict, dataclass


# ============================================================
# 检测规则
# ============================================================

# 这里只检测“比较明确”的广告或引流表达。
#
# 注意：
# - 检测到后只会标记为需要人工复核；
# - 不会由程序直接删除；
# - 最终是否删除仍由模型和人工检查决定。
AD_PATTERNS = (
    re.compile(
        r"扫码.{0,20}(?:关注|领取|添加|下载|购买|报名)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:关注|添加).{0,12}(?:公众号|微信|客服|老师|助教)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:免费|限时).{0,20}(?:领取|课程|资料|优惠|下载)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:购买|报名|咨询).{0,15}(?:课程|网课|客服|老师|助教)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:微信号|客服微信|老师微信|助教微信)\s*[:：]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:加群|入群|进群).{0,15}(?:学习|领取|资料|交流)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:淘宝|拼多多|闲鱼|抖音|快手|小红书).{0,20}"
        r"(?:购买|店铺|搜索|关注)",
        re.IGNORECASE,
    ),
)

# Markdown 标题。
HEADING_PATTERN = re.compile(
    r"(?m)^#{1,6}[ \t]+\S+"
)

# 常见题目格式。
#
# 这不是严格题目识别，只用于比较清洗前后的数量变化。
QUESTION_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:"
    r"第[ \t]*\d+[ \t]*题"
    r"|[0-9]{1,4}[.、．][ \t]*\S"
    r")"
)

# 数字、年份、小数和百分比。
#
# 用于发现教材中的法条编号、年份、题目数字等是否大量减少。
NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"\d+(?:\.\d+)?%?"
    r"(?![A-Za-z0-9_])"
)

# HTTP/HTTPS URL。
URL_PATTERN = re.compile(
    r"https?://[^\s<>()\[\]\"']+",
    re.IGNORECASE,
)

# Markdown 表格分隔行，例如：
#
# | --- | --- |
TABLE_SEPARATOR_PATTERN = re.compile(
    r"(?m)^[ \t]*\|?"
    r"[ \t]*:?-{3,}:?[ \t]*"
    r"(?:\|[ \t]*:?-{3,}:?[ \t]*)+"
    r"\|?[ \t]*$"
)


# ============================================================
# 报告结构
# ============================================================

@dataclass
class QualityReport:
    input_chars: int
    output_chars: int

    retained_ratio: float
    removed_ratio: float
    expansion_ratio: float

    input_headings: int
    output_headings: int

    input_questions: int
    output_questions: int

    input_numbers: int
    output_numbers: int

    input_urls: int
    output_urls: int
    added_urls: list[str]

    input_tables: int
    output_tables: int

    remaining_ad_signals: list[str]

    warnings: list[str]
    severe_errors: list[str]
    review_required: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


# ============================================================
# 基础工具
# ============================================================

def _count(
    pattern: re.Pattern[str],
    text: str,
) -> int:
    return len(pattern.findall(text))


def _unique_matches(
    pattern: re.Pattern[str],
    text: str,
    limit: int = 20,
) -> list[str]:
    values: list[str] = []

    for match in pattern.finditer(text):
        value = match.group(0).strip().rstrip(".,;，。；")

        if not value:
            continue

        if value not in values:
            values.append(value)

        if len(values) >= limit:
            break

    return values


def find_ad_signals(
    text: str,
    limit: int = 20,
) -> list[str]:
    """
    查找输出中仍然存在的疑似广告或引流文字。

    结果只用于风险提示，不自动删除任何内容。
    """
    signals: list[str] = []

    for pattern in AD_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0).strip()

            # 防止 metadata 和日志中保存过长文本。
            if len(value) > 100:
                value = value[:100] + "……"

            if value and value not in signals:
                signals.append(value)

            if len(signals) >= limit:
                return signals

    return signals


def find_added_urls(
    input_text: str,
    output_text: str,
) -> list[str]:
    """
    找出模型输出中新增加的 URL。

    原文已经存在的 URL 不视为新增。
    """
    input_urls = set(
        _unique_matches(
            URL_PATTERN,
            input_text,
            limit=1000,
        )
    )

    output_urls = _unique_matches(
        URL_PATTERN,
        output_text,
        limit=1000,
    )

    return [
        value
        for value in output_urls
        if value not in input_urls
    ][:20]


# ============================================================
# 质量评估
# ============================================================

def assess_quality(
    input_text: str,
    output_text: str,
) -> QualityReport:
    """
    比较模型输入和输出，识别可能发生的：

    - 严重截断；
    - 过度删除；
    - 异常扩写；
    - 标题大量减少；
    - 题目大量减少；
    - 数字大量减少；
    - 表格大量丢失；
    - 新增 URL；
    - 广告残留。

    普通问题进入 warnings，并标记 review_required。

    非常严重的问题进入 severe_errors。
    processor.py 会拒绝把严重异常结果保存为成功结果。
    """
    input_length = len(input_text)
    output_length = len(output_text)

    if input_length > 0:
        retained_ratio = output_length / input_length
        removed_ratio = max(
            0.0,
            1.0 - retained_ratio,
        )
        expansion_ratio = output_length / input_length
    else:
        retained_ratio = 1.0
        removed_ratio = 0.0
        expansion_ratio = 1.0

    input_headings = _count(
        HEADING_PATTERN,
        input_text,
    )
    output_headings = _count(
        HEADING_PATTERN,
        output_text,
    )

    input_questions = _count(
        QUESTION_PATTERN,
        input_text,
    )
    output_questions = _count(
        QUESTION_PATTERN,
        output_text,
    )

    input_numbers = _count(
        NUMBER_PATTERN,
        input_text,
    )
    output_numbers = _count(
        NUMBER_PATTERN,
        output_text,
    )

    input_urls = _count(
        URL_PATTERN,
        input_text,
    )
    output_urls = _count(
        URL_PATTERN,
        output_text,
    )

    input_tables = _count(
        TABLE_SEPARATOR_PATTERN,
        input_text,
    )
    output_tables = _count(
        TABLE_SEPARATOR_PATTERN,
        output_text,
    )

    added_urls = find_added_urls(
        input_text,
        output_text,
    )

    remaining_ad_signals = find_ad_signals(
        output_text
    )

    warnings: list[str] = []
    severe_errors: list[str] = []

    # --------------------------------------------------------
    # 内容长度
    # --------------------------------------------------------

    if input_length >= 200:
        if retained_ratio < 0.30:
            severe_errors.append(
                "输出不到输入的 30%，"
                "疑似发生严重截断或正文被过度删除"
            )
        elif retained_ratio < 0.50:
            warnings.append(
                "输出不到输入的 50%，"
                "可能删除了过多正文"
            )
        elif retained_ratio < 0.70:
            warnings.append(
                "输出不到输入的 70%，"
                "建议人工检查删除内容"
            )

        if expansion_ratio > 2.00:
            severe_errors.append(
                "输出超过输入的 200%，"
                "模型可能生成了大量原文不存在的内容"
            )
        elif expansion_ratio > 1.50:
            warnings.append(
                "输出超过输入的 150%，"
                "请检查模型是否扩写或补充内容"
            )

    # --------------------------------------------------------
    # 标题完整性
    # --------------------------------------------------------

    if (
        input_headings >= 3
        and output_headings < input_headings * 0.5
    ):
        warnings.append(
            "输出标题数量显著减少，"
            "请检查章节结构是否丢失"
        )

    # --------------------------------------------------------
    # 题目完整性
    # --------------------------------------------------------

    if (
        input_questions >= 3
        and output_questions < input_questions * 0.7
    ):
        warnings.append(
            "输出题目数量显著减少，"
            "请检查题干、选项、答案和解析"
        )

    # --------------------------------------------------------
    # 数字完整性
    # --------------------------------------------------------

    if (
        input_numbers >= 20
        and output_numbers < input_numbers * 0.70
    ):
        warnings.append(
            "输出数字数量显著减少，"
            "请检查年份、法条编号、题号、数值和单位"
        )

    # --------------------------------------------------------
    # 表格完整性
    # --------------------------------------------------------

    if (
        input_tables >= 2
        and output_tables < input_tables * 0.5
    ):
        warnings.append(
            "输出表格数量显著减少，"
            "请检查 Markdown 表格是否丢失或损坏"
        )

    # --------------------------------------------------------
    # URL 和广告
    # --------------------------------------------------------

    if added_urls:
        warnings.append(
            "输出新增了原文不存在的 URL，"
            "模型可能添加了链接"
        )

    if remaining_ad_signals:
        warnings.append(
            "输出中仍存在疑似广告或引流文字"
        )

    review_required = bool(
        warnings or severe_errors
    )

    return QualityReport(
        input_chars=input_length,
        output_chars=output_length,
        retained_ratio=round(
            retained_ratio,
            4,
        ),
        removed_ratio=round(
            removed_ratio,
            4,
        ),
        expansion_ratio=round(
            expansion_ratio,
            4,
        ),
        input_headings=input_headings,
        output_headings=output_headings,
        input_questions=input_questions,
        output_questions=output_questions,
        input_numbers=input_numbers,
        output_numbers=output_numbers,
        input_urls=input_urls,
        output_urls=output_urls,
        added_urls=added_urls,
        input_tables=input_tables,
        output_tables=output_tables,
        remaining_ad_signals=remaining_ad_signals,
        warnings=warnings,
        severe_errors=severe_errors,
        review_required=review_required,
    )
