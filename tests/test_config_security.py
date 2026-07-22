from __future__ import annotations

import pytest

from clean_auto.config import normalize_base_url


@pytest.mark.parametrize(
    "value",
    [
        "https://api.example.com/v1",
        "http://localhost:8000/v1",
        "http://127.0.0.1:8000/v1",
        "http://[::1]:8000/v1",
    ],
)
def test_base_url_allows_secure_or_loopback_endpoints(
    value: str,
) -> None:
    assert normalize_base_url(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "http://api.example.com/v1",
        "http://192.168.1.20:8000/v1",
    ],
)
def test_base_url_rejects_remote_plain_http(
    value: str,
) -> None:
    with pytest.raises(
        RuntimeError,
        match="远程接口必须使用 HTTPS",
    ):
        normalize_base_url(value)
