@echo off
setlocal EnableExtensions DisableDelayedExpansion
chcp 65001 >nul

set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

if defined RAG_CLEANER_HOME (
  set "BASE_DIR=%RAG_CLEANER_HOME%"
) else (
  set "BASE_DIR=%SCRIPT_DIR%"
)

for %%I in ("%BASE_DIR%\.") do set "BASE_DIR=%%~fI"
set "MENU_SCRIPT=%SCRIPT_DIR%scripts\menu.ps1"

if not exist "%MENU_SCRIPT%" (
  echo [ERROR] Menu script is missing: "%MENU_SCRIPT%"
  pause
  exit /b 1
)

where pwsh.exe >nul 2>nul
if not errorlevel 1 (
  set "POWERSHELL_EXE=pwsh.exe"
  goto START_MENU
)

where powershell.exe >nul 2>nul
if not errorlevel 1 (
  set "POWERSHELL_EXE=powershell.exe"
  goto START_MENU
)

type "%SCRIPT_DIR%scripts\powershell_missing.txt"
pause
exit /b 1

:START_MENU
"%POWERSHELL_EXE%" -NoProfile -ExecutionPolicy Bypass -File "%MENU_SCRIPT%" -BaseDir "%BASE_DIR%"
set "MENU_EXIT=%ERRORLEVEL%"
endlocal & exit /b %MENU_EXIT%
