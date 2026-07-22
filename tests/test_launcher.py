from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
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


def test_windows_menu_launcher_only_starts_powershell_menu() -> None:
    launcher_path = ROOT / "一键菜单.bat"
    launcher = launcher_path.read_bytes()
    text = launcher.decode("ascii")

    assert not launcher.startswith(b"\xef\xbb\xbf")
    assert launcher.count(b"\n") == launcher.count(b"\r\n")
    assert b"\r" not in launcher.replace(b"\r\n", b"")
    assert 'set "MENU_SCRIPT=%SCRIPT_DIR%scripts\\menu.ps1"' in text
    assert "where pwsh.exe" in text
    assert "where powershell.exe" in text
    assert '-File "%MENU_SCRIPT%"' in text
    assert 'type "%SCRIPT_DIR%scripts\\powershell_missing.txt"' in text
    assert "python.exe" not in text
    assert "clean_auto" not in text
    assert "Select 0-14" not in text
    assert "RAG_CLEANER_HOME" in text

    missing_message = (ROOT / "scripts" / "powershell_missing.txt").read_bytes()
    assert not missing_message.startswith(b"\xef\xbb\xbf")
    assert missing_message.count(b"\n") == missing_message.count(b"\r\n")
    assert "未检测到 PowerShell" in missing_message.decode("utf-8")


def test_powershell_menu_has_chinese_layout_and_safe_environment_gate() -> None:
    menu_path = ROOT / "scripts" / "menu.ps1"
    data = menu_path.read_bytes()
    script = data.decode("utf-8-sig")

    assert data.startswith(b"\xef\xbb\xbf")
    assert data.count(b"\n") == data.count(b"\r\n")
    assert b"\r" not in data.replace(b"\r\n", b"")
    for label in [
        "开始处理全部文件",
        "选择文件或文件夹",
        "继续或重试任务",
        "查看处理状态",
        "设置同时处理数量",
        "打开处理结果",
        "安装或修复运行环境",
        "更多功能",
        "退出",
    ]:
        assert label in script

    assert '$VenvPython = Join-Path $BaseDir ".venv\\Scripts\\python.exe"' in script
    assert "Test-ProjectPython" in script
    assert "请运行一键安装.bat" in script
    assert "Get-Command python.exe" not in script
    assert "Get-Command py.exe" not in script
    assert "OPENAI_API_KEY" not in script
    assert "sk-" not in script


def test_powershell_menu_preserves_cli_and_selection_contracts() -> None:
    script = (ROOT / "scripts" / "menu.ps1").read_text(encoding="utf-8-sig")

    assert "select_input_files.ps1" in script
    assert "-Mode files" in script
    assert "-Mode directory" in script
    assert "--selection-file" in script
    assert "--resume-batch" in script
    assert "--retry-failed" in script
    assert "--batch-status" in script
    assert "--dry-run" in script
    assert "--max-files" in script
    assert "--workers" in script
    assert "& $VenvPython @commandArguments" in script
    assert "-NoNewWindow" not in script
    assert "Test-MenuChoice" in script
    assert "Read-MenuChoice" in script
    assert "function Open-BatchLog" in script
    assert "function Invoke-Installer" in script
    assert "一键安装.bat" in script


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


def _label_block(menu: str, label: str, next_label: str) -> str:
    start = menu.index(f"\n:{label}\n")
    end = menu.index(f"\n:{next_label}\n", start + 1)
    return menu[start:end]


def test_windows_batch_files_have_archive_safe_crlf_bytes() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="ascii")
    assert "*.bat -text whitespace=cr-at-eol" in attributes
    assert "*.cmd -text whitespace=cr-at-eol" in attributes
    assert "scripts/menu.ps1 -text whitespace=cr-at-eol" in attributes
    assert "scripts/powershell_missing.txt -text whitespace=cr-at-eol" in attributes

    batch_files = sorted(ROOT.glob("*.bat"))
    assert batch_files
    for path in batch_files:
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), path
        assert b"\n" in data, path
        assert data.count(b"\n") == data.count(b"\r\n"), path
        assert b"\r" not in data.replace(b"\r\n", b""), path


def test_windows_menu_has_explicit_eof_and_invalid_input_paths() -> None:
    script = (ROOT / "scripts" / "menu.ps1").read_text(encoding="utf-8-sig")

    assert "function Read-MenuChoice" in script
    assert "return $null" in script
    assert "if ($null -eq $choice -or $choice -eq \"0\") { exit 0 }" in script
    assert "输入无效，未执行操作。" in script
    assert "输入无效，未启动处理任务。" in script


def test_windows_menu_dispatches_exact_choice_values_to_isolated_actions() -> None:
    script = (ROOT / "scripts" / "menu.ps1").read_text(encoding="utf-8-sig")

    assert '"1" { Invoke-Cleaner -Arguments @("--yes", "--workers", "$Workers") }' in script
    assert '"2" { Show-SelectionMenu }' in script
    assert '"3" { Show-RecoveryMenu }' in script
    assert '"4" { Invoke-Cleaner -Arguments @("--batch-status") }' in script
    assert '"5" { Set-MenuWorkers }' in script
    assert '"6" { Open-MenuDirectory -Path (Join-Path $BaseDir "output") }' in script
    assert '"7" { Invoke-Installer }' in script
    assert '"8" { Show-MoreMenu }' in script
    assert "[int]$choice" in script
    assert "@(\"1\", \"2\", \"3\", \"4\", \"5\")" in script


def test_windows_menu_blocks_actions_when_python_preflight_fails() -> None:
    script = (ROOT / "scripts" / "menu.ps1").read_text(encoding="utf-8-sig")

    assert "if (-not (Test-ProjectPython))" in script
    assert "Show-EnvironmentRequired" in script
    assert "不会使用系统 Python，也不会启动处理任务。" in script
    assert "Get-Command python.exe" not in script
    assert "Get-Command py.exe" not in script


@pytest.mark.skipif(sys.platform != "win32", reason="requires Windows PowerShell")
def test_powershell_menu_safely_handles_missing_venv_in_available_hosts(
    tmp_path: Path,
) -> None:
    menu_path = ROOT / "scripts" / "menu.ps1"
    hosts = [host for host in ("powershell.exe", "pwsh.exe") if shutil.which(host)]
    assert hosts

    for host in hosts:
        missing_venv_result = subprocess.run(
            [
                host,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(menu_path),
                "-BaseDir",
                str(tmp_path),
            ],
            capture_output=True,
            input="",
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=15,
        )
        assert missing_venv_result.returncode == 0, host
        assert "当前缺少可用 .venv，请选择 7 安装或修复运行环境" in missing_venv_result.stdout
        assert "正在启动处理命令" not in missing_venv_result.stdout
