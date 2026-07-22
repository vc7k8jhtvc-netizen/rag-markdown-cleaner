from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _assert_crlf(data: bytes) -> None:
    assert data.count(b"\n") == data.count(b"\r\n")
    assert b"\r" not in data.replace(b"\r\n", b"")


def test_installer_launchers_have_archive_safe_encoding() -> None:
    batch = (ROOT / "一键安装.bat").read_bytes()
    script = (ROOT / "scripts" / "install_environment.ps1").read_bytes()

    assert not batch.startswith(b"\xef\xbb\xbf")
    batch_text = batch.decode("ascii")
    assert 'set "INSTALL_SCRIPT=%SCRIPT_DIR%scripts\\install_environment.ps1"' in batch_text
    assert "where pwsh.exe" in batch_text
    assert "where powershell.exe" in batch_text
    assert '-File "%INSTALL_SCRIPT%"' in batch_text
    assert "python.exe" not in batch_text
    assert "pip" not in batch_text.lower()
    _assert_crlf(batch)
    assert script.startswith(b"\xef\xbb\xbf")
    _assert_crlf(script)


def test_installer_is_rooted_at_script_directory() -> None:
    script = (ROOT / "scripts" / "install_environment.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "$ProjectRoot = Split-Path -Parent $PSScriptRoot" in script
    assert '$VenvPath = Join-Path $ProjectRoot ".venv"' in script
    assert "Set-Location -LiteralPath $ProjectRoot" not in script
    assert "Get-Location" not in script


def test_installer_detects_python_in_required_order_and_reads_version() -> None:
    script = (ROOT / "scripts" / "install_environment.ps1").read_text(
        encoding="utf-8-sig"
    )

    py_launcher = script.index('"py.exe"')
    python_exe = script.index('"python.exe"')
    assert py_launcher < python_exe
    assert 'PrefixArguments = @("-3")' in script
    assert "python.exe" in script
    assert "sys.version_info" in script
    assert "3.10" in script
    assert "Python 3.10+" in script
    assert "python.exe -m pip" not in script


def test_installer_uses_only_project_venv_pip() -> None:
    script = (ROOT / "scripts" / "install_environment.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert '$VenvPython = Join-Path $VenvPath "Scripts\\python.exe"' in script
    assert '& $VenvPython -m pip' in script
    assert "-m pip install -e ." in script
    assert "pip.exe" not in script
    assert "python -m pip" not in script
    assert "--user" not in script


def test_installer_requires_explicit_confirmation_before_rebuild() -> None:
    script = (ROOT / "scripts" / "install_environment.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "function Read-RebuildConfirmation" in script
    assert "Read-Host" in script
    assert "Remove-Item -LiteralPath $VenvPath -Recurse" in script
    assert "if ($confirmation -ne \"Y\")" in script
    assert "Resolve-Path" in script
    assert "符号链接" in script
    assert "-LiteralPath $VenvPath" in script


def test_installer_checks_project_contracts_without_secrets() -> None:
    script = (ROOT / "scripts" / "install_environment.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert 'Join-Path $ProjectRoot "prompt.md"' in script
    assert 'foreach ($directoryName in @("input", "output", "logs"))' in script
    assert 'Join-Path $ProjectRoot $directoryName' in script
    assert "import clean_auto" in script
    assert "clean_auto.__version__" in script
    assert "OPENAI_API_KEY" not in script
    assert "os.environ" not in script
    assert "SetEnvironmentVariable" not in script
    assert "HKCU" not in script
    assert "HKLM" not in script
