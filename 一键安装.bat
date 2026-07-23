@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"
set "INSTALL_SCRIPT=%SCRIPT_DIR%scripts\install_environment.ps1"

if not exist "%INSTALL_SCRIPT%" (
  echo [ERROR] Install script is missing: "%INSTALL_SCRIPT%"
  pause
  exit /b 1
)

where pwsh.exe >nul 2>nul
if not errorlevel 1 (
  set "POWERSHELL_EXE=pwsh.exe"
  goto START_INSTALL
)

where powershell.exe >nul 2>nul
if not errorlevel 1 (
  set "POWERSHELL_EXE=powershell.exe"
  goto START_INSTALL
)

type "%SCRIPT_DIR%scripts\powershell_missing.txt"
pause
exit /b 1

:START_INSTALL
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%INSTALL_SCRIPT%"
set "INSTALL_EXIT=%ERRORLEVEL%"
echo.
if "%INSTALL_EXIT%"=="0" (
  echo Environment setup completed. You can run the menu launcher.
) else (
  echo Environment setup did not complete. Review the diagnostics and retry.
)
pause
endlocal & exit /b %INSTALL_EXIT%
