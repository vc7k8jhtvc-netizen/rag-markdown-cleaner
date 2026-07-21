from __future__ import annotations

import re
from typing import Any

from .config import (
    ALLOWED_SUBJECTS,
    FRONT_MATTER_STATUS,
    REQUIRED_FRONT_MATTER_FIELDS,
    SUSPICIOUS_PHRASES,
    yaml,
)


# ============================================================
# 正则规则
# ============================================================

OUTER_CODE_FENCE_PATTERN = re.compile(
    r"\A"
    r"```(?:markdown|md|text)?[ \t]*\r?\n"
    r"(.*?)"
    r"\r?\n```[ \t]*"
    r"\Z",
    flags=re.IGNORECASE | re.DOTALL,
)

FRONT_MATTER_PATTERN = re.compile(
    r"\A"
    r"---[ \t]*\r?\n"
    r"(.*?)"
    r"\r?\n---[ \t]*"
    r"(?:\r?\n|\Z)",
    flags=re.DOTALL,
)

MARKDOWN_HEADING_PATTERN = re.compile(
    r"(?m)^#{1,6}[ \t]+\S+"
)

HTML_DOCUMENT_PATTERN = re.compile(
    r"(?is)"
    r"^\s*(?:<!doctype\s+html[^>]*>\s*)?"
    r"<html(?:\s|>)"
)

HTML_ERROR_PATTERN = re.compile(
    r"(?is)"
    r"<(?:title|h1)[^>]*>"
    r"[^<]*(?:error|错误|bad gateway|service unavailable|forbidden)"
)

JSON_OBJECT_PATTERN = re.compile(
    r"^\s*\{.*\}\s*$",
    flags=re.DOTALL,
)

EXPLANATION_PREFIXES = (
    "以下是清洗后的内容",
    "以下是清洗后的 Markdown",
    "以下是处理后的内容",
    "以下是整理后的内容",
    "以下是转换后的内容",
    "这是清洗后的内容",
    "这是处理后的结果",
    "根据您的要求",
    "根据你的要求",
    "我已经完成",
    "我已完成",
    "已为您清洗",
    "已为你清洗",
    "当然可以",
    "好的，",
    "好的。",
)

ERROR_RESPONSE_PHRASES = (
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "internal server error",
    "access denied",
    "request failed",
    "rate limit exceeded",
    "unauthorized",
    "invalid api key",
    "上游服务错误",
    "服务暂时不可用",
    "请求失败",
    "请求超时",
    "鉴权失败",
    "无效的 api key",
)


# ============================================================
# 基础处理
# ============================================================

def remove_outer_code_fence(text: str) -> str:
    """
    移除模型输出最外层的 Markdown 代码块。

    仅当整个输出都被一个代码围栏包裹时才会移除，
    不会删除正文内部正常存在的代码块。

    支持：

    ```markdown
    正文
    ```

    ```md
    正文
    ```

    ```text
    正文
    ```
    """
    text = text.strip()

    match = OUTER_CODE_FENCE_PATTERN.fullmatch(text)

    if match:
        return match.group(1).strip()

    return text


def extract_front_matter(result: str) -> str | None:
    """
    提取 Markdown 开头的 YAML Front Matter。

    合法示例：

    ---
    title: 示例教材
    subject: 安全生产管理
    ---

    Front Matter 必须位于输出最前面。
    开头允许存在普通空白字符。
    """
    text = result.lstrip("\ufeff \t\r\n")

    match = FRONT_MATTER_PATTERN.match(text)

    if not match:
        return None

    return match.group(1)


def starts_like_front_matter(result: str) -> bool:
    """
    判断输出是否以 YAML 分隔符开头。

    用于区分：

    1. 完全没有 Front Matter；
    2. 看起来想生成 Front Matter，但没有正确闭合。
    """
    text = result.lstrip("\ufeff \t\r\n")

    return bool(
        re.match(
            r"^---(?:[ \t]*\r?\n|[ \t]*$)",
            text,
        )
    )


def count_front_matter_blocks(result: str) -> int:
    """
    粗略统计输出中 YAML 分隔块的数量。

    该统计只用于提示重复 YAML，不作为严格 Markdown 解析器。
    """
    separator_count = len(
        re.findall(
            r"(?m)^---[ \t]*$",
            result,
        )
    )

    return separator_count // 2


# ============================================================
# YAML 解析
# ============================================================

def parse_front_matter_fields(
    front_matter: str,
) -> dict[str, Any]:
    """
    解析 YAML Front Matter。

    与旧版本保持兼容：

    - 成功时返回字典；
    - 失败时返回空字典。

    需要具体错误信息时，内部校验使用
    parse_front_matter_with_error()。
    """
    fields, _ = parse_front_matter_with_error(
        front_matter
    )

    return fields


def parse_front_matter_with_error(
    front_matter: str,
) -> tuple[dict[str, Any], str | None]:
    """
    严格解析 YAML Front Matter。

    返回：

    - 字段字典；
    - 错误信息，成功时为 None。
    """
    if yaml is None:
        return {}, "PyYAML 不可用，无法可靠解析 YAML Front Matter"

    try:
        data = yaml.safe_load(front_matter)
    except Exception as exc:
        message = str(exc).replace(
            "\r",
            " ",
        ).replace(
            "\n",
            " ",
        )

        if len(message) > 300:
            message = message[:300] + "……"

        return {}, f"YAML 解析失败：{message}"

    if data is None:
        return {}, "YAML Front Matter 为空"

    if not isinstance(data, dict):
        return (
            {},
            "YAML Front Matter 必须是键值对象，"
            "不能是列表、字符串或其他类型",
        )

    return data, None


def normalize_field_value(
    value: Any,
) -> str | None:
    """
    将 YAML 标量字段转换为字符串。

    None 或空字符串统一返回 None。
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return str(value).lower()

    text = str(value).strip()

    return text if text else None


def is_scalar_or_null(value: Any) -> bool:
    """
    判断 YAML 字段是否是允许的简单值。

    教材元数据字段不应包含列表或嵌套对象。
    """
    return value is None or isinstance(
        value,
        (str, int, float),
    )


def validate_front_matter_fields(
    fields: dict[str, Any],
    strict: bool,
) -> tuple[list[str], list[str]]:
    """
    校验 YAML 字段的存在性、类型和值。

    strict=True 时，关键问题进入 errors；
    strict=False 时进入 warnings。
    """
    errors: list[str] = []
    warnings: list[str] = []

    def push(
        message: str,
        as_error: bool = strict,
    ) -> None:
        if as_error:
            errors.append(message)
        else:
            warnings.append(message)

    # --------------------------------------------------------
    # 必需字段
    # --------------------------------------------------------

    for field_name in REQUIRED_FRONT_MATTER_FIELDS:
        if field_name not in fields:
            push(
                f"YAML 缺少字段：{field_name}"
            )
            continue

        value = fields[field_name]

        if not is_scalar_or_null(value):
            push(
                f"YAML 字段 {field_name} "
                "必须是字符串、数字或 null，"
                "不能是列表或对象"
            )

    # --------------------------------------------------------
    # title
    # --------------------------------------------------------

    if "title" in fields:
        title_value = fields.get("title")

        if title_value is not None:
            if not isinstance(title_value, str):
                push(
                    "YAML 字段 title 必须是字符串或 null"
                )
            elif not title_value.strip():
                push(
                    "YAML 字段 title 不能为空字符串"
                )
            elif len(title_value.strip()) > 500:
                push(
                    "YAML 字段 title 异常过长",
                    as_error=False,
                )

    # --------------------------------------------------------
    # subject
    # --------------------------------------------------------

    subject = normalize_field_value(
        fields.get("subject")
    )

    if (
        subject
        and subject.lower() != "null"
        and subject not in ALLOWED_SUBJECTS
    ):
        push(
            f"subject 不在允许范围内：{subject}"
        )

    # --------------------------------------------------------
    # status
    # --------------------------------------------------------

    status = normalize_field_value(
        fields.get("status")
    )

    if (
        status
        and status.lower() != "null"
        and status != FRONT_MATTER_STATUS
    ):
        push(
            "status 不符合要求，"
            f"应为：{FRONT_MATTER_STATUS}"
        )

    # --------------------------------------------------------
    # year
    # --------------------------------------------------------

    if "year" in fields:
        year_value = fields.get("year")

        if isinstance(year_value, bool):
            push(
                "YAML 字段 year 不能是布尔值"
            )
        elif year_value is not None and not isinstance(
            year_value,
            (str, int),
        ):
            push(
                "YAML 字段 year 必须是年份字符串、整数或 null"
            )
        elif isinstance(year_value, int):
            if year_value < 1000 or year_value > 9999:
                push(
                    f"YAML 字段 year 疑似无效：{year_value}",
                    as_error=False,
                )
        elif isinstance(year_value, str):
            normalized_year = year_value.strip()

            if (
                normalized_year
                and normalized_year.lower() != "null"
                and not re.fullmatch(
                    r"\d{4}(?:\s*[-–—/]\s*\d{4})?",
                    normalized_year,
                )
            ):
                push(
                    f"YAML 字段 year 格式需要复核："
                    f"{normalized_year}",
                    as_error=False,
                )

    # --------------------------------------------------------
    # source 和 type
    # --------------------------------------------------------

    for field_name in ("source", "type"):
        if field_name not in fields:
            continue

        value = fields.get(field_name)

        if value is not None and not isinstance(
            value,
            str,
        ):
            push(
                f"YAML 字段 {field_name} "
                "必须是字符串或 null"
            )

    return errors, warnings


# ============================================================
# 异常输出检测
# ============================================================

def looks_like_html_error(result: str) -> bool:
    """
    判断结果是否像 HTML 错误页。
    """
    text = result.strip()

    if HTML_DOCUMENT_PATTERN.search(text):
        return True

    return bool(
        HTML_ERROR_PATTERN.search(
            text[:5000]
        )
    )


def looks_like_json_response(result: str) -> bool:
    """
    判断模型是否错误地返回了 JSON 对象。

    正常教材 Markdown 偶尔可能包含 JSON 示例，
    因此只有整个输出看起来都是 JSON 时才提示。
    """
    text = result.strip()

    if not JSON_OBJECT_PATTERN.fullmatch(text):
        return False

    lowered = text[:2000].lower()

    indicators = (
        '"error"',
        '"message"',
        '"content"',
        '"choices"',
        '"response"',
        '"output"',
    )

    return any(
        indicator in lowered
        for indicator in indicators
    )


def find_explanation_prefix(
    result: str,
) -> str | None:
    """
    检测模型在正文前添加的说明。

    只检查输出开头，减少对教材正文的误判。
    """
    text = result.lstrip()

    # 如果正确地以 YAML 或 Markdown 标题开头，
    # 通常不是模型附加说明。
    if starts_like_front_matter(text):
        return None

    if MARKDOWN_HEADING_PATTERN.match(text):
        return None

    head = text[:500].lower()

    for phrase in EXPLANATION_PREFIXES:
        if phrase.lower() in head[:150]:
            return phrase

    for phrase in SUSPICIOUS_PHRASES:
        if phrase.lower() in head[:300]:
            return phrase

    return None


def find_error_response_phrase(
    result: str,
) -> str | None:
    """
    检测 API 错误文字是否被误当作模型正文。
    """
    text = result.strip().lower()

    # 只检查较短结果或开头区域。
    # 教材正文中偶尔可能讨论“请求失败”等技术概念，
    # 不应仅凭正文中出现这些词就判定失败。
    if len(text) > 5000:
        search_area = text[:1000]
    else:
        search_area = text

    for phrase in ERROR_RESPONSE_PHRASES:
        if phrase in search_area:
            return phrase

    return None


# ============================================================
# 主校验函数
# ============================================================

def validate_result(
    result: str,
    input_chunk: str,
    strict_validation: bool,
    part_number: int,
) -> tuple[list[str], list[str]]:
    """
    校验模型输出。

    返回：

    - errors：阻止保存的问题；
    - warnings：保存结果，但要求人工复核的问题。

    第 1 分片：

    - strict 模式下必须包含完整 YAML Front Matter；
    - 宽松模式下缺少或损坏 YAML 会记录警告。

    后续分片：

    - 正常情况下不应重复 YAML Front Matter；
    - 若存在 YAML，会检查其合法性并提示复核。
    """
    errors: list[str] = []
    warnings: list[str] = []

    result = result.strip()

    if not result:
        errors.append("输出为空")
        return errors, warnings

    def push(
        message: str,
        as_error: bool,
    ) -> None:
        if as_error:
            errors.append(message)
        else:
            warnings.append(message)

    is_first_part = part_number == 1

    # --------------------------------------------------------
    # 错误页和异常响应
    # --------------------------------------------------------

    if looks_like_html_error(result):
        errors.append(
            "模型输出疑似 HTML 错误页，"
            "没有得到有效 Markdown"
        )

    if looks_like_json_response(result):
        errors.append(
            "模型输出疑似 API JSON 响应，"
            "没有得到有效 Markdown"
        )

    error_phrase = find_error_response_phrase(
        result
    )

    if error_phrase is not None:
        # 若结果非常短，基本可以确定不是教材正文。
        if len(result) < 1000:
            errors.append(
                "输出疑似服务错误信息："
                f"{error_phrase}"
            )
        else:
            warnings.append(
                "输出开头出现疑似服务错误文字："
                f"{error_phrase}"
            )

    # --------------------------------------------------------
    # 说明性前缀
    # --------------------------------------------------------

    explanation_prefix = find_explanation_prefix(
        result
    )

    if explanation_prefix is not None:
        warnings.append(
            "输出开头可能包含模型附加说明："
            f"{explanation_prefix}"
        )

    # --------------------------------------------------------
    # Front Matter
    # --------------------------------------------------------

    front_matter = extract_front_matter(
        result
    )

    if front_matter is None:
        if starts_like_front_matter(result):
            push(
                "YAML Front Matter 未正确闭合或格式损坏",
                as_error=(
                    is_first_part
                    and strict_validation
                ),
            )
        elif is_first_part:
            push(
                "没有检测到完整 YAML Front Matter",
                as_error=strict_validation,
            )
    else:
        fields, yaml_error = (
            parse_front_matter_with_error(
                front_matter
            )
        )

        should_be_error = (
            is_first_part
            and strict_validation
        )

        if yaml_error is not None:
            push(
                yaml_error,
                as_error=should_be_error,
            )
        else:
            (
                field_errors,
                field_warnings,
            ) = validate_front_matter_fields(
                fields,
                strict=should_be_error,
            )

            errors.extend(field_errors)
            warnings.extend(field_warnings)

        if not is_first_part:
            warnings.append(
                "后续分片意外包含 YAML Front Matter，"
                "合并完整文档时可能产生重复 YAML"
            )

    front_matter_blocks = count_front_matter_blocks(
        result
    )

    if front_matter_blocks > 1:
        warnings.append(
            "输出中检测到多个疑似 YAML Front Matter，"
            "请检查是否发生重复"
        )

    # --------------------------------------------------------
    # 长度检查
    # --------------------------------------------------------

    # 更详细的长度检查由 quality.py 完成。
    # 这里保留基础提示，以兼容直接调用本函数的场景。
    if (
        len(input_chunk) >= 200
        and len(result) < len(input_chunk) * 0.5
    ):
        warnings.append(
            "输出长度不到输入的 50%，"
            "请检查是否发生内容截断"
        )

    # --------------------------------------------------------
    # 代码围栏检查
    # --------------------------------------------------------

    if result.startswith("```"):
        warnings.append(
            "输出仍以代码围栏开头，"
            "请检查模型是否使用了不受支持的围栏类型"
        )

    # --------------------------------------------------------
    # 去重，保持原有顺序
    # --------------------------------------------------------

    errors = list(
        dict.fromkeys(errors)
    )
    warnings = list(
        dict.fromkeys(warnings)
    )

    return errors, warnings
