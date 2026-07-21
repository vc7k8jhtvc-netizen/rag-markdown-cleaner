@echo off
setlocal EnableExtensions
cd /d "%~dp0"

:MENU
cls
echo ========================================
echo   RAG Markdown Cleaner
echo   Dir: %cd%
echo ========================================
echo.

if exist "pause.flag" (
  echo   Pause status: REQUESTED
) else (
  echo   Pause status: OFF
)

if exist "stop.flag" (
  echo   Stop status: REQUESTED
) else (
  echo   Stop status: OFF
)

echo.
echo   [1] Dry-run preview
echo   [2] Test 1 file
echo   [3] Run all
echo   [4] Open input
echo   [5] Open output
echo   [6] Open log
echo   [7] Reset pause and stop flags
echo   [0] Exit
echo.

set "choice="
set /p "choice=Select 0-7: "

if "%choice%"=="1" goto DRYRUN
if "%choice%"=="2" goto RUN1
if "%choice%"=="3" goto RUNALL
if "%choice%"=="4" goto OPEN_INPUT
if "%choice%"=="5" goto OPEN_OUTPUT
if "%choice%"=="6" goto OPEN_LOG
if "%choice%"=="7" goto RESET_FLAGS
if "%choice%"=="0" goto END

echo.
echo Invalid option.
pause
goto MENU


:DRYRUN
call :RUN_CMD --dry-run
goto MENU


:RUN1
call :CHECK_RUN_FLAGS
if errorlevel 1 goto MENU

call :RUN_CMD --yes --max-files 1
goto MENU


:RUNALL
call :CHECK_RUN_FLAGS
if errorlevel 1 goto MENU

echo.
echo This will call the API for ALL pending files.
set "confirm="
set /p "confirm=Type Y to continue: "

if /i not "%confirm%"=="Y" (
  echo.
  echo Cancelled.
  pause
  goto MENU
)

call :RUN_CMD --yes
goto MENU


:OPEN_INPUT
if not exist "input" mkdir "input"
start "" "%cd%\input"
goto MENU


:OPEN_OUTPUT
if not exist "output" mkdir "output"
start "" "%cd%\output"
goto MENU


:OPEN_LOG
if not exist "logs" mkdir "logs"

if exist "logs\batch.jsonl" (
  start "" notepad "logs\batch.jsonl"
) else (
  echo.
  echo No log yet: logs\batch.jsonl
  pause
)

goto MENU


:RESET_FLAGS
echo.
echo This removes pause.flag and stop.flag.
echo Only continue after the cleaner process has fully stopped.
echo.
set "confirm="
set /p "confirm=Type Y to reset control flags: "

if /i not "%confirm%"=="Y" (
  echo.
  echo Cancelled.
  pause
  goto MENU
)

if exist "pause.flag" del /f /q "pause.flag"
if exist "stop.flag" del /f /q "stop.flag"

echo.
echo Pause and stop flags have been reset.
pause
goto MENU


:CHECK_RUN_FLAGS
if exist "stop.flag" (
  echo.
  echo [BLOCKED] stop.flag still exists.
  echo The cleaner would stop immediately after startup.
  echo Use menu option 7 after the previous process has ended.
  pause
  exit /b 1
)

if exist "pause.flag" (
  echo.
  echo [BLOCKED] pause.flag still exists.
  echo Use menu option 7, or run Continue.bat first.
  pause
  exit /b 1
)

exit /b 0


:RUN_CMD
echo.
echo [RUN] python -m clean_auto %*
echo.

if exist ".venv\Scripts\python.exe" (
  ".venv\Scripts\python.exe" -m clean_auto %*
) else (
  where python >nul 2>nul

  if errorlevel 1 (
    echo [ERROR] Python was not found.
    echo Create .venv or install Python first.
    pause
    exit /b 9009
  )

  echo [WARN] .venv not found, using system Python.
  python -m clean_auto %*
)

set "ERR=%ERRORLEVEL%"

echo.
echo ----------------------------------------
echo Done. Exit code: %ERR%
echo ----------------------------------------

if "%ERR%"=="0" (
  echo Status: completed successfully.
) else if "%ERR%"=="1" (
  echo Status: stopped or startup failed.
) else if "%ERR%"=="2" (
  echo Status: completed with failed items.
) else (
  echo Status: unexpected error.
)

pause
exit /b %ERR%


:END
endlocal
exit /b 0
