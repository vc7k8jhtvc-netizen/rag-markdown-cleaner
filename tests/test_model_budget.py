from __future__ import annotations

import pytest

from clean_auto.model_budget import (
    ModelBudget,
    apply_output_token_limit,
    estimate_request_budget,
    estimate_text_tokens,
    validate_request_budget,
)


def make_budget(
    context_window: int = 32768,
    max_output_tokens: int = 8000,
) -> ModelBudget:
    return ModelBudget(
        context_window=context_window,
        max_output_tokens=(
            max_output_tokens
        ),
        token_parameter="max_tokens",
        safety_margin_tokens=1024,
    )


def test_estimate_text_tokens_is_positive() -> None:
    assert estimate_text_tokens(
        "安全生产管理"
    ) > 0

    assert estimate_text_tokens("") == 1


def test_request_budget_fits() -> None:
    budget = make_budget()

    estimate = estimate_request_budget(
        system_prompt="系统提示词",
        user_message="教材正文" * 100,
        budget=budget,
    )

    assert estimate.fits
    assert (
        estimate.estimated_total_tokens
        <= budget.context_window
    )


def test_request_budget_rejects_overflow() -> None:
    budget = make_budget(
        context_window=1000,
        max_output_tokens=500,
    )

    with pytest.raises(
        RuntimeError,
        match="超过模型上下文容量",
    ):
        validate_request_budget(
            system_prompt="系统提示词" * 100,
            user_message="教材正文" * 1000,
            budget=budget,
        )


def test_output_limit_is_applied() -> None:
    payload: dict[str, object] = {
        "model": "test-model",
    }

    budget = make_budget(
        max_output_tokens=8000,
    )

    apply_output_token_limit(
        payload,
        budget,
    )

    assert payload["max_tokens"] == 8000


def test_zero_output_limit_is_omitted() -> None:
    payload: dict[str, object] = {
        "model": "test-model",
    }

    budget = make_budget(
        max_output_tokens=0,
    )

    apply_output_token_limit(
        payload,
        budget,
    )

    assert "max_tokens" not in payload
