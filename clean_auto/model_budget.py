from __future__ import annotations

import math
import os
import re
from dataclasses import dataclass


CJK_PATTERN = re.compile(
    "["
    "\u3400-\u4dbf"
    "\u4e00-\u9fff"
    "\uf900-\ufaff"
    "\u3040-\u30ff"
    "\uac00-\ud7af"
    "]"
)

ALLOWED_TOKEN_PARAMETERS = {
    "max_tokens",
    "max_completion_tokens",
}


@dataclass(frozen=True)
class ModelBudget:
    """
    模型上下文预算。

    context_window:
        模型的总上下文 token 数。
        0 表示不进行上下文上限检查。

    max_output_tokens:
        请求允许的最大输出 token 数。
        0 表示不向 API 请求添加输出 token 参数。

    token_parameter:
        API 使用的输出 token 参数名称。
    """

    context_window: int
    max_output_tokens: int
    token_parameter: str
    safety_margin_tokens: int

    @property
    def context_check_enabled(self) -> bool:
        return self.context_window > 0

    @property
    def output_limit_enabled(self) -> bool:
        return self.max_output_tokens > 0


@dataclass(frozen=True)
class BudgetEstimate:
    system_prompt_chars: int
    user_message_chars: int
    estimated_input_tokens: int
    reserved_output_tokens: int
    safety_margin_tokens: int
    estimated_total_tokens: int
    context_window: int
    remaining_tokens: int | None

    @property
    def fits(self) -> bool:
        if self.context_window <= 0:
            return True

        return (
            self.estimated_total_tokens
            <= self.context_window
        )


def _read_non_negative_int(
    variable_name: str,
    default: int,
) -> int:
    """
    读取非负整数环境变量。
    """
    raw_value = os.getenv(
        variable_name,
        str(default),
    ).strip()

    try:
        value = int(raw_value)
    except ValueError as exc:
        raise RuntimeError(
            f"{variable_name} 必须是整数，"
            f"当前值：{raw_value!r}"
        ) from exc

    if value < 0:
        raise RuntimeError(
            f"{variable_name} 不能小于 0"
        )

    return value


def load_model_budget() -> ModelBudget:
    """
    从环境变量加载模型预算。

    支持：

    OPENAI_CONTEXT_WINDOW
    OPENAI_MAX_OUTPUT_TOKENS
    OPENAI_TOKEN_PARAMETER
    OPENAI_SAFETY_MARGIN_TOKENS

    默认值说明：

    - context_window=0：
      不假设用户模型的上下文容量。

    - max_output_tokens=0：
      不自动向未知兼容接口添加 token 参数。

    用户确认模型能力后，应在 .env 中显式配置。
    """
    context_window = _read_non_negative_int(
        "OPENAI_CONTEXT_WINDOW",
        0,
    )

    max_output_tokens = _read_non_negative_int(
        "OPENAI_MAX_OUTPUT_TOKENS",
        0,
    )

    safety_margin_tokens = _read_non_negative_int(
        "OPENAI_SAFETY_MARGIN_TOKENS",
        1024,
    )

    token_parameter = os.getenv(
        "OPENAI_TOKEN_PARAMETER",
        "max_tokens",
    ).strip()

    if (
        token_parameter
        not in ALLOWED_TOKEN_PARAMETERS
    ):
        allowed = ", ".join(
            sorted(
                ALLOWED_TOKEN_PARAMETERS
            )
        )

        raise RuntimeError(
            "OPENAI_TOKEN_PARAMETER 只能是："
            f"{allowed}"
        )

    if (
        context_window > 0
        and max_output_tokens > 0
        and (
            max_output_tokens
            + safety_margin_tokens
            >= context_window
        )
    ):
        raise RuntimeError(
            "OPENAI_MAX_OUTPUT_TOKENS 与 "
            "OPENAI_SAFETY_MARGIN_TOKENS 之和"
            "必须小于 OPENAI_CONTEXT_WINDOW"
        )

    return ModelBudget(
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        token_parameter=token_parameter,
        safety_margin_tokens=(
            safety_margin_tokens
        ),
    )


def estimate_text_tokens(
    text: str,
) -> int:
    """
    粗略估算文本 token 数。

    这不是模型官方 tokenizer，只用于提前发现明显超限。

    估算规则：

    - 中日韩字符按约 1.2 token 计算；
    - 其他字符按约 4 字符一个 token 计算；
    - 最少返回 1。

    对中文教材采用略保守估算，宁可提前阻止明显超限请求，
    也不把估算值当作精确计费数据。
    """
    if not text:
        return 1

    cjk_chars = len(
        CJK_PATTERN.findall(text)
    )

    other_chars = max(
        0,
        len(text) - cjk_chars,
    )

    estimated = (
        cjk_chars * 1.2
        + other_chars / 4.0
    )

    return max(
        1,
        math.ceil(estimated),
    )


def estimate_request_budget(
    system_prompt: str,
    user_message: str,
    budget: ModelBudget,
) -> BudgetEstimate:
    """
    估算一次请求的上下文占用。

    额外增加少量消息结构开销，
    用于覆盖 role、JSON 消息格式等内容。
    """
    system_tokens = estimate_text_tokens(
        system_prompt
    )

    user_tokens = estimate_text_tokens(
        user_message
    )

    message_overhead = 64

    estimated_input_tokens = (
        system_tokens
        + user_tokens
        + message_overhead
    )

    reserved_output_tokens = (
        budget.max_output_tokens
        if budget.max_output_tokens > 0
        else 0
    )

    estimated_total_tokens = (
        estimated_input_tokens
        + reserved_output_tokens
        + budget.safety_margin_tokens
    )

    remaining_tokens: int | None

    if budget.context_window > 0:
        remaining_tokens = (
            budget.context_window
            - estimated_total_tokens
        )
    else:
        remaining_tokens = None

    return BudgetEstimate(
        system_prompt_chars=len(
            system_prompt
        ),
        user_message_chars=len(
            user_message
        ),
        estimated_input_tokens=(
            estimated_input_tokens
        ),
        reserved_output_tokens=(
            reserved_output_tokens
        ),
        safety_margin_tokens=(
            budget.safety_margin_tokens
        ),
        estimated_total_tokens=(
            estimated_total_tokens
        ),
        context_window=(
            budget.context_window
        ),
        remaining_tokens=remaining_tokens,
    )


def validate_request_budget(
    system_prompt: str,
    user_message: str,
    budget: ModelBudget,
) -> BudgetEstimate:
    """
    检查请求是否明显超过模型上下文容量。

    检查失败时，不发送 API 请求，
    从而避免无意义请求和费用。
    """
    estimate = estimate_request_budget(
        system_prompt=system_prompt,
        user_message=user_message,
        budget=budget,
    )

    if not estimate.fits:
        raise RuntimeError(
            "请求预计超过模型上下文容量："
            f"输入约 "
            f"{estimate.estimated_input_tokens:,} tokens，"
            f"预留输出 "
            f"{estimate.reserved_output_tokens:,} tokens，"
            f"安全余量 "
            f"{estimate.safety_margin_tokens:,} tokens，"
            f"合计约 "
            f"{estimate.estimated_total_tokens:,} tokens，"
            f"模型上下文配置为 "
            f"{estimate.context_window:,} tokens。"
            "请减小 --max-chars，"
            "或修正 .env 中的模型预算配置。"
        )

    return estimate


def apply_output_token_limit(
    payload: dict[str, object],
    budget: ModelBudget,
) -> None:
    """
    将输出 token 限制加入 API 请求。

    max_output_tokens=0 时不修改请求，
    兼容不支持这些参数的第三方接口。
    """
    if not budget.output_limit_enabled:
        return

    payload[
        budget.token_parameter
    ] = budget.max_output_tokens
