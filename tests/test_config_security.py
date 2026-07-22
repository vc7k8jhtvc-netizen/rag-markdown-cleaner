from __future__ import annotations

import re
from pathlib import Path

import pytest

from clean_auto.config import (
    load_runtime_config,
    normalize_base_url,
    parse_args,
)


def _write_prompt(base_dir: Path) -> Path:
    prompt_path = base_dir / "prompt.md"
    prompt_path.write_bytes(
        "系统提示词\r\n保留换行。\r\n".encode("utf-8")
    )
    return prompt_path


def test_load_runtime_config_uses_explicit_base_dir(
    tmp_path: Path,
) -> None:
    prompt_path = _write_prompt(tmp_path)
    args = parse_args(
        ["--dry-run", "--base-dir", str(tmp_path)]
    )

    config = load_runtime_config(args)

    assert config.base_dir == tmp_path.resolve()
    assert config.system_prompt == "系统提示词\r\n保留换行。"
    assert prompt_path == config.base_dir / "prompt.md"


def test_load_runtime_config_uses_rag_cleaner_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_prompt(tmp_path)
    monkeypatch.setenv("RAG_CLEANER_HOME", str(tmp_path))
    args = parse_args(["--dry-run"])

    config = load_runtime_config(args)

    assert config.base_dir == tmp_path.resolve()
    assert config.system_prompt == "系统提示词\r\n保留换行。"


def test_load_runtime_config_reports_missing_prompt_path(
    tmp_path: Path,
) -> None:
    expected_path = (tmp_path / "prompt.md").resolve()
    args = parse_args(
        ["--dry-run", "--base-dir", str(tmp_path)]
    )

    with pytest.raises(
        RuntimeError,
        match=re.escape(f"找不到提示词文件：{expected_path}"),
    ):
        load_runtime_config(args)


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
