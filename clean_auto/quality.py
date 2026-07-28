from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass

from .quality_settings import load_quality_thresholds


AD_PATTERNS = (
    re.compile(
        r"扫码.{0,20}(?:关注|领取|添加|下载|购买|报名)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:关注|添加).{0,12}"
        r"(?:公众号|微信|客服|老师|助教)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:免费|限时).{0,20}"
        r"(?:领取|课程|资料|优惠|下载)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:购买|报名|咨询).{0,15}"
        r"(?:课程|网课|客服|老师|助教)",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:微信号|客服微信|老师微信|助教微信)"
        r"\s*[:：]",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:加群|入群|进群).{0,15}"
        r"(?:学习|领取|资料|交流)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:淘宝|拼多多|闲鱼|抖音|快手|小红书)"
        r".{0,20}(?:购买|店铺|搜索|关注)",
        re.IGNORECASE,
    ),
)

HEADING_PATTERN = re.compile(
    r"(?m)^#{1,6}[ \t]+\S+"
)

QUESTION_PATTERN = re.compile(
    r"(?m)^[ \t]*(?:"
    r"第[ \t]*\d+[ \t]*题"
    r"|[0-9]{1,4}[.、．][ \t]*\S"
    r")"
)

NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"\d+(?:\.\d+)?%?"
    r"(?![A-Za-z0-9_])"
)

PROTECTED_NUMBER_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])"
    r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)"
    r"(?:\.\d+)?%?"
    r"(?![A-Za-z0-9_])"
)

PROTECTED_UNIT_PATTERN = re.compile(
    r"(?<![A-Za-z])(?:"
    r"°C|℃|°F|K|"
    r"MPa|kPa|Pa|GPa|"
    r"mg/m[³3]|g/m[³3]|kg/m[³3]|"
    r"m/s|km/h|m[²2]|m[³3]|"
    r"mm|cm|km|m|"
    r"μm|µm|nm|"
    r"μg|µg|mg|kg|g|"
    r"mL|L|"
    r"kW|MW|W|"
    r"kV|mV|V|"
    r"mA|A|Hz|dB|"
    r"min|ms|s|h"
    r")(?![A-Za-z])",
    re.IGNORECASE,
)

STANDARD_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?:GB|GB/T|AQ|AQ/T|HG|HG/T|JB|JB/T|"
    r"SY|SY/T|DL|DL/T|NB|NB/T|SH|SH/T)"
    r"\s*\d+(?:\.\d+)*(?:-\d{2,4})?"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

URL_PATTERN = re.compile(
    r"https?://[^\s<>()\[\]\"']+",
    re.IGNORECASE,
)

TABLE_SEPARATOR_PATTERN = re.compile(
    r"(?m)^[ \t]*\|?"
    r"[ \t]*:?-{3,}:?[ \t]*"
    r"(?:\|[ \t]*:?-{3,}:?[ \t]*)+"
    r"\|?[ \t]*$"
)

FRONT_MATTER_PATTERN = re.compile(
    r"\A---[ \t]*(?:\r\n|\n|\r)"
    r".*?"
    r"(?:\r\n|\n|\r)---[ \t]*"
    r"(?:(?:\r\n|\n|\r)|\Z)",
    re.DOTALL,
)


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
    protected_values_changed: bool

    input_urls: int
    output_urls: int
    added_urls: list[str]
    removed_urls: list[str]

    protected_anchor_ratio: float

    input_tables: int
    output_tables: int

    remaining_ad_signals: list[str]

    warnings: list[str]
    severe_errors: list[str]
    review_required: bool

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _count(
    pattern: re.Pattern[str],
    text: str,
) -> int:
    return len(pattern.findall(text))


def _normalize_url(value: str) -> str:
    """
    用于比较 URL，避免只因尾部标点变化导致误报新增链接。
    """
    return value.rstrip(
        ".,;:，。；：)]}）】》」』"
    ).lower()


def _is_ad_line(line: str) -> bool:
    return any(pattern.search(line) for pattern in AD_PATTERNS)


def _protected_text(text: str) -> str:
    """
    排除允许清理的广告行，保留需要做内容完整性检查的正文。

    只有命中既有广告规则的整行才被排除，避免把含少量广告词的
    教材正文整体视为可删除区域。
    """
    return "\n".join(
        line
        for line in text.splitlines()
        if not _is_ad_line(line)
    )


def _comparison_texts(
    input_text: str,
    output_text: str,
) -> tuple[str, str]:
    """
    返回正文完整性检查使用的输入和输出。

    第一分片允许按既有契约新增 YAML Front Matter；只有输入本来没有
    Front Matter 时才排除输出新增头部。输入已有的头部仍受完整保护。
    """
    if FRONT_MATTER_PATTERN.match(input_text):
        return input_text, output_text

    output_match = FRONT_MATTER_PATTERN.match(output_text)
    if output_match is None:
        return input_text, output_text

    return input_text, output_text[output_match.end() :]


def _normalize_protected_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    return normalized.replace(",", "").lower()


def _protected_values(text: str) -> list[str]:
    """
    按出现顺序提取受保护正文中的数字和 URL。

    数字覆盖题号、年份、日期组成部分、百分比、标准编号中的数字、
    带符号数值及小数；NFKC 归一化允许全角和半角形式互换。
    """
    protected = _protected_text(text)
    matches: list[tuple[int, str]] = []

    for match in PROTECTED_NUMBER_PATTERN.finditer(protected):
        matches.append(
            (
                match.start(),
                _normalize_protected_value(match.group(0)),
            )
        )

    for pattern in (PROTECTED_UNIT_PATTERN, STANDARD_ID_PATTERN):
        for match in pattern.finditer(protected):
            matches.append(
                (
                    match.start(),
                    _normalize_protected_value(match.group(0)),
                )
            )

    for match in URL_PATTERN.finditer(protected):
        matches.append(
            (
                match.start(),
                _normalize_url(match.group(0)),
            )
        )

    matches.sort(key=lambda item: item[0])
    return [value for _, value in matches]


def _normalized_anchor_text(text: str) -> str:
    protected = unicodedata.normalize("NFKC", _protected_text(text))
    return re.sub(r"\s+", "", protected).lower()


def _matched_anchor_count(
    anchors: set[str],
    candidate: str,
    anchor_size: int,
) -> int:
    """用滚动哈希单次扫描候选正文，避免为每个锚点重复遍历全文。"""
    if not anchors or len(candidate) < anchor_size:
        return 0

    mask = (1 << 64) - 1
    base = 257
    leading_power = pow(base, anchor_size - 1, 1 << 64)
    by_hash: dict[int, list[str]] = {}

    for anchor in anchors:
        value = 0
        for char in anchor:
            value = ((value * base) + ord(char)) & mask
        by_hash.setdefault(value, []).append(anchor)

    matched: set[str] = set()
    window_hash = 0
    for char in candidate[:anchor_size]:
        window_hash = ((window_hash * base) + ord(char)) & mask

    final_start = len(candidate) - anchor_size
    for start in range(final_start + 1):
        possible = by_hash.get(window_hash)
        if possible:
            window = candidate[start : start + anchor_size]
            for anchor in possible:
                if window == anchor:
                    matched.add(anchor)
            if len(matched) == len(anchors):
                break

        if start == final_start:
            continue

        outgoing = ord(candidate[start])
        incoming = ord(candidate[start + anchor_size])
        window_hash = (
            (
                window_hash
                - (outgoing * leading_power)
            )
            * base
            + incoming
        ) & mask

    return len(matched)


def _protected_anchor_ratio(
    input_text: str,
    output_text: str,
    *,
    anchor_size: int = 24,
    max_anchors: int = 200,
) -> float:
    """
    用分布在全文的稳定字符锚点检测同长度的大面积替换。

    允许局部 OCR 修正和广告删除；正文被整体替换为长度、结构近似
    的另一篇内容时，锚点命中比例会快速下降。
    """
    source = _normalized_anchor_text(input_text)
    candidate = _normalized_anchor_text(output_text)

    if len(source) < anchor_size:
        return 1.0 if source == candidate else 0.0

    possible = len(source) - anchor_size + 1
    anchor_count = min(max_anchors, possible)
    if anchor_count <= 1:
        starts = [0]
    else:
        starts = [
            round(index * (possible - 1) / (anchor_count - 1))
            for index in range(anchor_count)
        ]

    anchors = {
        source[start : start + anchor_size]
        for start in starts
    }
    if not anchors:
        return 1.0

    matched = _matched_anchor_count(
        anchors,
        candidate,
        anchor_size,
    )
    return matched / len(anchors)


def _unique_matches(
    pattern: re.Pattern[str],
    text: str,
    limit: int = 20,
) -> list[str]:
    values: list[str] = []

    for match in pattern.finditer(text):
        value = match.group(0).strip()

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

    检测结果只用于复核提示，不自动删除内容。
    """
    signals: list[str] = []

    for pattern in AD_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(0).strip()

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
    找出输出中新增的 URL。

    URL 尾部标点变化不视为新增。
    """
    input_urls = {
        _normalize_url(value)
        for value in _unique_matches(
            URL_PATTERN,
            input_text,
            limit=1000,
        )
    }

    added_urls: list[str] = []

    for value in _unique_matches(
        URL_PATTERN,
        output_text,
        limit=1000,
    ):
        if _normalize_url(value) in input_urls:
            continue

        added_urls.append(value)

        if len(added_urls) >= 20:
            break

    return added_urls


def find_removed_urls(
    input_text: str,
    output_text: str,
) -> list[str]:
    """找出受保护正文中被删除的 URL。"""
    output_urls = {
        _normalize_url(value)
        for value in _unique_matches(
            URL_PATTERN,
            _protected_text(output_text),
            limit=1000,
        )
    }
    removed_urls: list[str] = []

    for value in _unique_matches(
        URL_PATTERN,
        _protected_text(input_text),
        limit=1000,
    ):
        if _normalize_url(value) in output_urls:
            continue
        removed_urls.append(value)
        if len(removed_urls) >= 20:
            break

    return removed_urls


def assess_quality(
    input_text: str,
    output_text: str,
) -> QualityReport:
    """
    比较输入与输出，检测明显风险。

    阈值从环境变量读取，未配置时使用项目默认值。
    """
    thresholds = load_quality_thresholds()
    comparison_input, comparison_output = _comparison_texts(
        input_text,
        output_text,
    )

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
    protected_input_values = _protected_values(comparison_input)
    protected_output_values = _protected_values(comparison_output)
    protected_values_changed = (
        protected_input_values != protected_output_values
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
        comparison_input,
        comparison_output,
    )
    removed_urls = find_removed_urls(
        comparison_input,
        comparison_output,
    )
    protected_anchor_ratio = _protected_anchor_ratio(
        comparison_input,
        comparison_output,
    )

    remaining_ad_signals = find_ad_signals(
        output_text
    )

    warnings: list[str] = []
    severe_errors: list[str] = []

    if protected_values_changed:
        severe_errors.append(
            "受保护正文中的数字、单位、标准编号或 URL "
            "内容、数量或出现顺序发生变化，"
            "请检查法条编号、年份、限值、答案和链接"
        )

    if input_length >= 200 and protected_anchor_ratio < 0.35:
        severe_errors.append(
            "受保护正文的稳定锚点保留率低于 35%，"
            "疑似发生大范围替换、重写或重排"
        )

    if input_length >= 200:
        if (
            retained_ratio
            < thresholds.severe_min_retained_ratio
        ):
            severe_errors.append(
                "输出保留比例低于严重阈值 "
                f"{thresholds.severe_min_retained_ratio:.0%}，"
                "疑似发生严重截断或正文被过度删除"
            )

        elif (
            retained_ratio
            < thresholds.warning_min_retained_ratio
        ):
            warnings.append(
                "输出保留比例低于警告阈值 "
                f"{thresholds.warning_min_retained_ratio:.0%}，"
                "可能删除了过多正文"
            )

        elif (
            retained_ratio
            < thresholds.review_min_retained_ratio
        ):
            warnings.append(
                "输出保留比例低于复核阈值 "
                f"{thresholds.review_min_retained_ratio:.0%}，"
                "建议人工检查删除内容"
            )

        if (
            expansion_ratio
            > thresholds.severe_max_expansion_ratio
        ):
            severe_errors.append(
                "输出扩写比例超过严重阈值 "
                f"{thresholds.severe_max_expansion_ratio:.0%}，"
                "模型可能生成了大量原文不存在的内容"
            )

        elif (
            expansion_ratio
            > thresholds.warning_max_expansion_ratio
        ):
            warnings.append(
                "输出扩写比例超过复核阈值 "
                f"{thresholds.warning_max_expansion_ratio:.0%}，"
                "请检查模型是否扩写或补充内容"
            )

    if (
        input_headings >= 3
        and output_headings
        < input_headings
        * thresholds.heading_retained_ratio
    ):
        warnings.append(
            "输出标题数量显著减少，"
            "请检查章节结构是否丢失"
        )

    if (
        input_questions >= 3
        and output_questions
        < input_questions
        * thresholds.question_retained_ratio
    ):
        warnings.append(
            "输出题目数量显著减少，"
            "请检查题干、选项、答案和解析"
        )

    if (
        input_numbers >= 20
        and output_numbers
        < input_numbers
        * thresholds.number_retained_ratio
    ):
        warnings.append(
            "输出数字数量显著减少，"
            "请检查年份、法条编号、题号、数值和单位"
        )

    if (
        input_tables >= 2
        and output_tables
        < input_tables
        * thresholds.table_retained_ratio
    ):
        warnings.append(
            "输出表格数量显著减少，"
            "请检查 Markdown 表格是否丢失或损坏"
        )

    if added_urls:
        warnings.append(
            "输出新增了原文不存在的 URL，"
            "模型可能添加了链接"
        )

    if removed_urls:
        warnings.append(
            "受保护正文中的 URL 被删除，"
            "请检查来源链接是否丢失"
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
        protected_values_changed=protected_values_changed,
        input_urls=input_urls,
        output_urls=output_urls,
        added_urls=added_urls,
        removed_urls=removed_urls,
        protected_anchor_ratio=round(
            protected_anchor_ratio,
            4,
        ),
        input_tables=input_tables,
        output_tables=output_tables,
        remaining_ad_signals=remaining_ad_signals,
        warnings=warnings,
        severe_errors=severe_errors,
        review_required=review_required,
    )
