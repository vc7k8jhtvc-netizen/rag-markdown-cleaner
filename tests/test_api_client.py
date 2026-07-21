from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import httpx
import pytest

from clean_auto.api_client import (
    ApiClient,
    extract_content,
    parse_retry_after,
)


def disable_model_budget(
    monkeypatch,
) -> None:
    """
    关闭测试中的模型预算限制。

    测试不会读取真实 .env，也不会访问真实 API。
    """
    monkeypatch.setenv(
        "OPENAI_CONTEXT_WINDOW",
        "0",
    )
    monkeypatch.setenv(
        "OPENAI_MAX_OUTPUT_TOKENS",
        "0",
    )
    monkeypatch.setenv(
        "OPENAI_SAFETY_MARGIN_TOKENS",
        "1024",
    )
    monkeypatch.setenv(
        "OPENAI_TOKEN_PARAMETER",
        "max_tokens",
    )


def build_sse_response(
    request: httpx.Request,
    text: str = "清洗后的教材正文。",
) -> httpx.Response:
    """
    构造一个标准 OpenAI 兼容 SSE 响应。
    """
    first_event = {
        "choices": [
            {
                "delta": {
                    "content": text,
                },
                "finish_reason": None,
            }
        ]
    }

    final_event = {
        "choices": [
            {
                "delta": {},
                "finish_reason": "stop",
            }
        ]
    }

    body = (
        "data: "
        + json.dumps(
            first_event,
            ensure_ascii=False,
        )
        + "\n\n"
        + "data: "
        + json.dumps(
            final_event,
            ensure_ascii=False,
        )
        + "\n\n"
        + "data: [DONE]\n\n"
    ).encode("utf-8")

    return httpx.Response(
        status_code=200,
        headers={
            "Content-Type": (
                "text/event-stream"
            ),
        },
        content=body,
        request=request,
    )


def install_mock_transport(
    client: ApiClient,
    handler: Callable[
        [httpx.Request],
        httpx.Response,
    ],
) -> None:
    """
    把 ApiClient 的网络层替换成内存 MockTransport。
    """
    client._client.close()

    client._client = httpx.Client(
        transport=httpx.MockTransport(
            handler
        ),
        timeout=httpx.Timeout(
            10.0,
        ),
    )


def test_extract_content_from_delta() -> None:
    choice = {
        "delta": {
            "content": "教材正文",
        }
    }

    assert extract_content(
        choice
    ) == "教材正文"


def test_extract_content_from_content_list() -> None:
    choice = {
        "message": {
            "content": [
                {
                    "text": "第一段",
                },
                {
                    "text": {
                        "value": "第二段",
                    },
                },
                "第三段",
            ]
        }
    }

    assert extract_content(
        choice
    ) == "第一段第二段第三段"


def test_parse_retry_after_seconds() -> None:
    request = httpx.Request(
        "POST",
        "https://example.com/v1/chat/completions",
    )

    response = httpx.Response(
        status_code=429,
        headers={
            "Retry-After": "12.5",
        },
        request=request,
    )

    assert parse_retry_after(
        response
    ) == 12.5


def test_successful_sse_stream(
    monkeypatch,
    tmp_path: Path,
) -> None:
    disable_model_budget(
        monkeypatch
    )

    request_count = 0

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal request_count
        request_count += 1

        assert request.method == "POST"
        assert (
            request.url.path
            == "/v1/chat/completions"
        )

        authorization = (
            request.headers.get(
                "Authorization"
            )
        )

        assert authorization == (
            "Bearer test-key"
        )

        return build_sse_response(
            request,
            text="清洗后的安全生产教材正文。",
        )

    client = ApiClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
    )

    install_mock_transport(
        client,
        handler,
    )

    try:
        result = client.stream_request(
            system_prompt="系统提示词",
            user_message="待清洗教材正文",
            file_index=1,
            total_files=1,
            part_number=1,
            total_parts=1,
            pause_file=(
                tmp_path / "pause.flag"
            ),
            stop_file=(
                tmp_path / "stop.flag"
            ),
            partial_path=(
                tmp_path / "partial.md"
            ),
            sleep_fn=lambda *_args: None,
        )
    finally:
        client.close()

    assert request_count == 1
    assert (
        result.text
        == "清洗后的安全生产教材正文。"
    )
    assert result.received_events == 2
    assert result.received_chars == len(
        "清洗后的安全生产教材正文。"
    )
    assert result.truncated is False


def test_http_429_is_retried(
    monkeypatch,
    tmp_path: Path,
) -> None:
    disable_model_budget(
        monkeypatch
    )

    request_count = 0
    sleep_values: list[float] = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal request_count
        request_count += 1

        if request_count == 1:
            return httpx.Response(
                status_code=429,
                headers={
                    "Retry-After": "0",
                },
                content=(
                    b'{"error":"rate limit"}'
                ),
                request=request,
            )

        return build_sse_response(
            request,
            text="重试后成功。",
        )

    def fake_sleep(
        seconds: float,
        pause_file: Path,
        stop_file: Path,
    ) -> None:
        del pause_file
        del stop_file

        sleep_values.append(
            seconds
        )

    client = ApiClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
    )

    install_mock_transport(
        client,
        handler,
    )

    try:
        result = client.stream_request(
            system_prompt="系统提示词",
            user_message="教材正文",
            file_index=1,
            total_files=1,
            part_number=1,
            total_parts=1,
            pause_file=(
                tmp_path / "pause.flag"
            ),
            stop_file=(
                tmp_path / "stop.flag"
            ),
            partial_path=(
                tmp_path / "partial.md"
            ),
            sleep_fn=fake_sleep,
        )
    finally:
        client.close()

    assert request_count == 2
    assert result.text == "重试后成功。"
    assert sleep_values == [0.0]


def test_http_401_is_not_retried(
    monkeypatch,
    tmp_path: Path,
) -> None:
    disable_model_budget(
        monkeypatch
    )

    request_count = 0

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        nonlocal request_count
        request_count += 1

        return httpx.Response(
            status_code=401,
            content=(
                b'{"error":"invalid api key"}'
            ),
            request=request,
        )

    client = ApiClient(
        base_url="https://example.com/v1",
        api_key="invalid-test-key",
        model="test-model",
    )

    install_mock_transport(
        client,
        handler,
    )

    try:
        with pytest.raises(
            RuntimeError,
            match="HTTP 401",
        ):
            client.stream_request(
                system_prompt="系统提示词",
                user_message="教材正文",
                file_index=1,
                total_files=1,
                part_number=1,
                total_parts=1,
                pause_file=(
                    tmp_path / "pause.flag"
                ),
                stop_file=(
                    tmp_path / "stop.flag"
                ),
                partial_path=(
                    tmp_path / "partial.md"
                ),
                sleep_fn=lambda *_args: None,
            )
    finally:
        client.close()

    assert request_count == 1
