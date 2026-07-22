from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import clean_auto.batch_manifest as batch_manifest
import clean_auto.pipeline as pipeline
from clean_auto.config import parse_args, validate_args
from clean_auto.selection import (
    SELECTION_SCHEMA,
    SELECTION_SCHEMA_VERSION,
    load_selection_paths,
)


ROOT = Path(__file__).resolve().parents[1]
BATCH_ID = "20260723T120000000000Z-0123456789ab"


@pytest.mark.parametrize(
    "argv",
    [
        ["--batch-status", "--selection-file", "selected.json"],
        ["--batch-status", "--resume-batch"],
        ["--batch-status", "--retry-failed"],
        ["--batch-status", "--dry-run"],
    ],
)
def test_batch_status_rejects_processing_modes(argv: list[str]) -> None:
    with pytest.raises(RuntimeError):
        validate_args(parse_args(argv))


def _forbid_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pipeline,
        "load_runtime_config",
        lambda _args: pytest.fail("batch status must not load API configuration"),
    )
    monkeypatch.setattr(
        pipeline,
        "acquire_lock",
        lambda *_args, **_kwargs: pytest.fail("batch status must not lock"),
    )
    monkeypatch.setattr(
        pipeline,
        "ApiClient",
        lambda *_args, **_kwargs: pytest.fail("batch status must not create API client"),
    )


def test_batch_status_without_latest_is_read_only_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _forbid_processing(monkeypatch)

    pipeline.main(["--batch-status", "--base-dir", str(tmp_path)])

    assert "暂无批次记录" in capsys.readouterr().out
    assert not (tmp_path / "logs").exists()


def test_batch_status_prints_safe_summary_without_modifying_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=[
            "pending.md",
            "running.md",
            "succeeded.md",
            "failed.md",
            "skipped.md",
            "interrupted.md",
        ],
        selection_source="scan",
        workers=3,
        batch_id=BATCH_ID,
        timestamp="2026-07-23T12:00:00",
    )
    for path, status in [
        ("running.md", "running"),
        ("succeeded.md", "succeeded"),
        ("failed.md", "failed"),
        ("skipped.md", "skipped"),
        ("interrupted.md", "interrupted"),
    ]:
        batch_manifest.update_file(
            log_dir,
            manifest,
            path,
            status=status,
            error=(
                "private-error-marker source body prompt text"
                if status == "failed"
                else None
            ),
            timestamp="2026-07-23T12:30:00",
        )

    manifest_path = batch_manifest.manifest_path(log_dir, BATCH_ID)
    latest_path = batch_manifest.latest_path(log_dir)
    manifest_bytes = manifest_path.read_bytes()
    latest_bytes = latest_path.read_bytes()
    _forbid_processing(monkeypatch)

    pipeline.main(["--batch-status", "--base-dir", str(tmp_path)])

    output = capsys.readouterr().out
    expected = {
        "batch_id": BATCH_ID,
        "status": "running",
        "workers": "3",
        "total": "6",
        "pending": "1",
        "running": "1",
        "succeeded": "1",
        "failed": "1",
        "skipped": "1",
        "interrupted": "1",
        "created_at": "2026-07-23T12:00:00",
        "updated_at": "2026-07-23T12:30:00",
    }
    for key, value in expected.items():
        assert f"{key}: {value}" in output
    assert "private-error-marker" not in output
    assert "source body" not in output
    assert "prompt text" not in output
    assert manifest_path.read_bytes() == manifest_bytes
    assert latest_path.read_bytes() == latest_bytes


def test_batch_status_rejects_corrupt_latest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    latest = batch_manifest.latest_path(tmp_path / "logs")
    latest.parent.mkdir(parents=True)
    latest.write_bytes(b"not-json")
    _forbid_processing(monkeypatch)

    with pytest.raises(RuntimeError, match="latest.*JSON"):
        pipeline.main(["--batch-status", "--base-dir", str(tmp_path)])


def test_batch_status_rejects_missing_target_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_manifest.write_latest(
        tmp_path / "logs",
        BATCH_ID,
        timestamp="2026-07-23T12:00:00",
    )
    _forbid_processing(monkeypatch)

    with pytest.raises(RuntimeError, match="不存在"):
        pipeline.main(["--batch-status", "--base-dir", str(tmp_path)])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("schema", "unknown", "schema"),
        ("schema_version", 2, "schema_version"),
    ],
)
def test_batch_status_rejects_unsupported_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
    message: str,
) -> None:
    log_dir = tmp_path / "logs"
    manifest = batch_manifest.create_manifest(
        log_dir=log_dir,
        relative_paths=["sample.md"],
        selection_source="scan",
        batch_id=BATCH_ID,
        timestamp="2026-07-23T12:00:00",
    )
    manifest[field] = value
    batch_manifest.manifest_path(log_dir, BATCH_ID).write_bytes(
        json.dumps(manifest).encode("utf-8")
    )
    _forbid_processing(monkeypatch)

    with pytest.raises(RuntimeError, match=message):
        pipeline.main(["--batch-status", "--base-dir", str(tmp_path)])


def test_selection_contract_accepts_unicode_and_spaces(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "input"
    first = input_dir / "法规 文件" / "安全生产法.md"
    second = input_dir / "真题" / "2025 test.md"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"first\n")
    second.write_bytes(b"second\n")
    selection_file = tmp_path / "logs" / "selections" / "menu.json"
    selection_file.parent.mkdir(parents=True)
    selection_file.write_bytes(
        json.dumps(
            {
                "schema": SELECTION_SCHEMA,
                "schema_version": SELECTION_SCHEMA_VERSION,
                "source": {"kind": "files", "root": None},
                "paths": ["法规 文件/安全生产法.md", "真题/2025 test.md"],
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )

    assert load_selection_paths(selection_file, input_dir) == [
        first.resolve(),
        second.resolve(),
    ]


def test_windows_menu_exposes_batch_controls_with_quoted_paths() -> None:
    menu_path = ROOT / "一键菜单.bat"
    menu = menu_path.read_text(encoding="utf-8")
    required_labels = [
        "Select Markdown files",
        "Select input subdirectory",
        "Resume latest batch",
        "Retry failed files from latest batch",
        "Set workers",
        "Show latest batch status",
        "Open logs directory",
    ]
    for label in required_labels:
        assert label in menu

    assert '--selection-file "%SELECTION_FILE%"' in menu
    assert '--workers "%WORKERS%"' in menu
    assert "--resume-batch" in menu
    assert "--retry-failed" in menu
    assert "--batch-status" in menu
    assert '--base-dir "%BASE_DIR%"' in menu
    assert 'for %%I in ("%BASE_DIR%\\.")' in menu
    assert '-File "%SELECTOR_SCRIPT%"' in menu
    assert '"%BASE_DIR%\\input"' in menu
    assert '"%BASE_DIR%\\output"' in menu
    assert '"%BASE_DIR%\\logs"' in menu
    assert "RAG_CLEANER_HOME" in menu
    assert "powershell.exe" in menu
    assert "pwsh.exe" in menu
    assert "--files" not in menu


def test_windows_menu_references_existing_selector() -> None:
    menu = (ROOT / "一键菜单.bat").read_text(encoding="utf-8")
    match = re.search(
        r'set "SELECTOR_SCRIPT=%SCRIPT_DIR%([^\"]+)"',
        menu,
    )
    assert match is not None
    selector = ROOT.joinpath(*match.group(1).split("\\"))
    assert selector.is_file()


def test_powershell_selector_has_stable_safe_contract() -> None:
    selector_path = ROOT / "scripts" / "select_input_files.ps1"
    script = selector_path.read_text(encoding="utf-8")

    schema = re.search(r'\$SelectionSchema = "([^\"]+)"', script)
    version = re.search(r"\$SelectionSchemaVersion = ([0-9]+)", script)
    assert schema is not None and schema.group(1) == SELECTION_SCHEMA
    assert version is not None and int(version.group(1)) == SELECTION_SCHEMA_VERSION
    assert 'ValidateSet("files", "directory")' in script
    assert "OpenFileDialog" in script
    assert "FolderBrowserDialog" in script
    assert "Multiselect = $true" in script
    assert "ConvertTo-Json" in script
    assert "[System.IO.File]::Move" in script
    assert "[guid]::NewGuid()" in script
    assert "exit 2" in script
    assert "OPENAI_API_KEY" not in script
    assert "sk-" not in script
    assert "C:\\Users\\" not in script
