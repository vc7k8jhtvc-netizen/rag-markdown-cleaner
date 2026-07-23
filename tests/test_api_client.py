from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

import httpx
import pytest

from clean_auto.api_client import (
    ApiClient,
    build_user_message,
    extract_content,
    parse_retry_after,
)
from clean_auto.progress import ProgressContext, ProgressReporter, format_progress_event


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


def test_request_waiting_event_is_visible_before_mock_response_is_released(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disable_model_budget(monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    reporter = ProgressReporter()
    context = ProgressContext(
        file_index=1,
        total_files=2,
        relative_path=Path("法规/安全生产法.md"),
        part_number=1,
        total_parts=16,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        assert release.wait(timeout=2)
        return build_sse_response(request)

    client = ApiClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
    )
    install_mock_transport(client, handler)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(
            client.stream_request,
            system_prompt="系统提示词",
            user_message="教材正文",
            file_index=1,
            total_files=2,
            part_number=1,
            total_parts=16,
            pause_file=tmp_path / "pause.flag",
            stop_file=tmp_path / "stop.flag",
            reporter=reporter,
            context=context,
        )
        assert entered.wait(timeout=2)
        waiting_events = reporter.drain()
        assert [event.kind for event in waiting_events] == ["chunk_started"]
        assert format_progress_event(waiting_events[0]) == (
            "[1/2] 处理中：法规/安全生产法.md"
            "（分片 1/16，正在请求并等待模型返回）"
        )
        assert capsys.readouterr().out == ""
        release.set()
        future.result(timeout=2)

    client.close()


def test_crlf_chunk_is_preserved_in_api_user_message(
    monkeypatch,
    tmp_path: Path,
) -> None:
    disable_model_budget(
        monkeypatch
    )
    chunk = "# 标题\r\n\r\n第一段正文。\r\n"
    user_message = build_user_message(
        chunk=chunk,
        part_number=1,
        total_parts=1,
        relative_path=Path("source.md"),
    )
    received_user_messages: list[str] = []

    def handler(
        request: httpx.Request,
    ) -> httpx.Response:
        payload = json.loads(
            request.content.decode("utf-8")
        )
        received_user_message = payload[
            "messages"
        ][1]["content"]
        received_user_messages.append(
            received_user_message
        )

        assert received_user_message == user_message
        assert chunk in received_user_message

        return build_sse_response(
            request,
            text="清洗后的正文。",
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
        client.stream_request(
            system_prompt="系统提示词",
            user_message=user_message,
            file_index=1,
            total_files=1,
            part_number=1,
            total_parts=1,
            pause_file=tmp_path / "pause.flag",
            stop_file=tmp_path / "stop.flag",
            partial_path=tmp_path / "partial.md",
            sleep_fn=lambda *_args: None,
        )
    finally:
        client.close()

    assert received_user_messages == [
        user_message
    ]


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


def test_http_429_retry_event_keeps_file_and_chunk_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    disable_model_budget(monkeypatch)
    request_count = 0
    reporter = ProgressReporter()
    context = ProgressContext(
        file_index=2,
        total_files=3,
        relative_path=Path("技术 教材.md"),
        part_number=1,
        total_parts=45,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal request_count
        request_count += 1
        if request_count == 1:
            return httpx.Response(
                status_code=429,
                headers={"Retry-After": "0"},
                content=b'{"error":"rate limit"}',
                request=request,
            )
        return build_sse_response(request, text="重试后成功。")

    client = ApiClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
    )
    install_mock_transport(client, handler)
    try:
        client.stream_request(
            system_prompt="系统提示词",
            user_message="教材正文",
            file_index=2,
            total_files=3,
            part_number=1,
            total_parts=45,
            pause_file=tmp_path / "pause.flag",
            stop_file=tmp_path / "stop.flag",
            sleep_fn=lambda *_args: None,
            reporter=reporter,
            context=context,
        )
    finally:
        client.close()

    assert capsys.readouterr().out == ""
    lines = [format_progress_event(event) for event in reporter.drain()]
    assert lines == [
        "[2/3] 处理中：技术 教材.md（分片 1/45，正在请求并等待模型返回）",
        "[2/3] 重试中：技术 教材.md（分片 1/45，第 1/3 次，等待 0 秒）",
        "[2/3] 处理中：技术 教材.md（分片 1/45，正在请求并等待模型返回）",
    ]


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


def test_partial_progress_uses_filename_without_absolute_path(
    tmp_path: Path,
) -> None:
    client = ApiClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
    )
    reporter = ProgressReporter()
    context = ProgressContext(
        file_index=1,
        total_files=1,
        relative_path=Path("input/sample.md"),
    )
    error = RuntimeError("request failed")
    error.partial_text = "partial output"  # type: ignore[attr-defined]
    partial_path = tmp_path / "partial" / "sample.partial.md"
    try:
        client._save_partial_if_available(
            error,
            partial_path,
            reporter,
            context,
        )
    finally:
        client.close()

    line = format_progress_event(reporter.drain()[0])
    assert "sample.partial.md" in line
    assert str(tmp_path) not in line


def test_configured_request_concurrency_never_exceeds_workers(
    monkeypatch,
    tmp_path: Path,
) -> None:
    disable_model_budget(monkeypatch)
    active = 0
    maximum = 0
    entered = threading.Event()
    release = threading.Event()
    guard = threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, maximum
        with guard:
            active += 1
            maximum = max(maximum, active)
            if active == 2:
                entered.set()
        release.wait(timeout=2)
        with guard:
            active -= 1
        return build_sse_response(request, text="ok")

    client = ApiClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
    )
    client.configure_concurrency(2)
    install_mock_transport(client, handler)

    def request(index: int) -> None:
        client.stream_request(
            system_prompt="prompt",
            user_message=f"source {index}",
            file_index=index,
            total_files=3,
            part_number=1,
            total_parts=1,
            pause_file=tmp_path / "pause.flag",
            stop_file=tmp_path / "stop.flag",
            sleep_fn=lambda *_args: None,
        )

    try:
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(request, index) for index in range(3)]
            assert entered.wait(timeout=2)
            assert maximum == 2
            release.set()
            for future in futures:
                future.result(timeout=2)
    finally:
        client.close()

    assert maximum == 2


def test_shared_rate_limit_cooldown_blocks_until_deadline(
    monkeypatch,
    tmp_path: Path,
) -> None:
    disable_model_budget(monkeypatch)
    current = [10.0]
    wait_values: list[float] = []
    wait_entered = threading.Event()
    advance_clock = threading.Event()
    cooldown_passed = threading.Event()

    class AdvancingCondition:
        def __enter__(self) -> AdvancingCondition:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def wait(self, timeout: float | None = None) -> None:
            assert timeout is not None
            wait_entered.set()
            assert advance_clock.wait(timeout=2)
            wait_values.append(timeout)
            current[0] += timeout

        def notify_all(self) -> None:
            return None

    client = ApiClient(
        base_url="https://example.com/v1",
        api_key="test-key",
        model="test-model",
    )
    client._monotonic = lambda: current[0]
    client._cooldown_condition = AdvancingCondition()

    try:
        client._extend_rate_limit_cooldown(3.0)
        waiter = threading.Thread(
            target=lambda: (
                client._wait_for_shared_cooldown(tmp_path / "stop.flag"),
                cooldown_passed.set(),
            )
        )
        waiter.start()
        assert wait_entered.wait(timeout=2)
        assert cooldown_passed.is_set() is False
        advance_clock.set()
        waiter.join(timeout=2)
        assert not waiter.is_alive()
    finally:
        client.close()

    assert cooldown_passed.is_set() is True
    assert current[0] >= 13.0
    assert sum(wait_values) == pytest.approx(3.0)
