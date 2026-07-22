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
set "SELECTOR_SCRIPT=%SCRIPT_DIR%scripts\select_input_files.ps1"
set "WORKERS=1"
call :DETECT_POWERSHELL
call :DETECT_PYTHON
if errorlevel 1 goto STARTUP_ERROR

:STARTUP_ERROR
if not defined PYTHON_EXE (
  echo.
  echo [ERROR] No usable Python environment can import clean_auto.pipeline.
  echo Create a virtual environment and install the project before starting the menu:
  echo   py -3 -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -e .
  pause
  goto END_ERROR
)

:MENU
cls
echo ========================================
echo   RAG Markdown Cleaner
echo   Base dir: "%BASE_DIR%"
echo   Workers: %WORKERS%
echo   Python: %PYTHON_EXE%
echo ========================================
echo.

if exist "%BASE_DIR%\pause.flag" (
  echo   Pause status: REQUESTED
) else (
  echo   Pause status: OFF
)

if exist "%BASE_DIR%\stop.flag" (
  echo   Stop status: REQUESTED
) else (
  echo   Stop status: OFF
)

if defined POWERSHELL_EXE (
  echo   Selector: %POWERSHELL_EXE%
) else (
  echo   Selector: unavailable
)

echo.
echo   [1] Dry-run preview
echo   [2] Test 1 file
echo   [3] Run all
echo   [4] Open input
echo   [5] Open output
echo   [6] Open batch log
echo   [7] Reset pause and stop flags
echo   [8] Select Markdown files
echo   [9] Select input subdirectory
echo   [10] Resume latest batch
echo   [11] Retry failed files from latest batch
echo   [12] Set workers (current: %WORKERS%)
echo   [13] Show latest batch status
echo   [14] Open logs directory
echo   [0] Exit
echo.

set "choice="
set /p "choice=Select 0-14: "
set "READ_ERROR=%ERRORLEVEL%"

if not defined choice (
  if "%READ_ERROR%"=="0" goto INVALID_OPTION
  goto INPUT_CLOSED
)
if not "%READ_ERROR%"=="0" goto INPUT_CLOSED

if "%choice%"=="1" goto DRYRUN
if "%choice%"=="2" goto RUN1
if "%choice%"=="3" goto RUNALL
if "%choice%"=="4" goto OPEN_INPUT
if "%choice%"=="5" goto OPEN_OUTPUT
if "%choice%"=="6" goto OPEN_LOG
if "%choice%"=="7" goto RESET_FLAGS
if "%choice%"=="8" goto SELECT_FILES
if "%choice%"=="9" goto SELECT_DIRECTORY
if "%choice%"=="10" goto RESUME_LATEST
if "%choice%"=="11" goto RETRY_FAILED
if "%choice%"=="12" goto SET_WORKERS
if "%choice%"=="13" goto BATCH_STATUS
if "%choice%"=="14" goto OPEN_LOGS
if "%choice%"=="0" goto END

:INVALID_OPTION
echo.
echo Invalid option.
pause
goto MENU


:INPUT_CLOSED
echo.
echo [ERROR] Menu input is closed. Exiting without starting a task.
goto END_ERROR


:DRYRUN
echo.
echo Dry-run always uses workers=1.
call :RUN_CMD --dry-run --workers 1
goto MENU


:RUN1
call :CHECK_RUN_FLAGS
if errorlevel 1 goto MENU

call :RUN_CMD --yes --max-files 1 --workers "%WORKERS%"
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

call :RUN_CMD --yes --workers "%WORKERS%"
goto MENU


:SELECT_FILES
call :CHECK_RUN_FLAGS
if errorlevel 1 goto MENU

call :CREATE_SELECTION files
if errorlevel 1 goto MENU

call :RUN_CMD --yes --workers "%WORKERS%" --selection-file "%SELECTION_FILE%"
goto MENU


:SELECT_DIRECTORY
call :CHECK_RUN_FLAGS
if errorlevel 1 goto MENU

call :CREATE_SELECTION directory
if errorlevel 1 goto MENU

call :RUN_CMD --yes --workers "%WORKERS%" --selection-file "%SELECTION_FILE%"
goto MENU


:RESUME_LATEST
call :CHECK_RUN_FLAGS
if errorlevel 1 goto MENU

call :RUN_CMD --yes --resume-batch --workers "%WORKERS%"
goto MENU


:RETRY_FAILED
call :CHECK_RUN_FLAGS
if errorlevel 1 goto MENU

call :RUN_CMD --yes --retry-failed --workers "%WORKERS%"
goto MENU


:SET_WORKERS
echo.
echo Current workers: %WORKERS%
set "NEW_WORKERS="
set /p "NEW_WORKERS=Enter workers (1-5): "

if "%NEW_WORKERS%"=="1" goto APPLY_WORKERS
if "%NEW_WORKERS%"=="2" goto APPLY_WORKERS
if "%NEW_WORKERS%"=="3" goto APPLY_WORKERS
if "%NEW_WORKERS%"=="4" goto APPLY_WORKERS
if "%NEW_WORKERS%"=="5" goto APPLY_WORKERS

echo.
echo [ERROR] Workers must be an integer from 1 to 5.
pause
goto SET_WORKERS


:APPLY_WORKERS
set "WORKERS=%NEW_WORKERS%"
echo.
echo Workers set to %WORKERS% for this menu session.
pause
goto MENU


:BATCH_STATUS
call :RUN_CMD --batch-status
goto MENU


:OPEN_INPUT
call :OPEN_DIRECTORY "%BASE_DIR%\input"
goto MENU


:OPEN_OUTPUT
call :OPEN_DIRECTORY "%BASE_DIR%\output"
goto MENU


:OPEN_LOGS
call :OPEN_DIRECTORY "%BASE_DIR%\logs"
goto MENU


:OPEN_LOG
if not exist "%BASE_DIR%\logs" mkdir "%BASE_DIR%\logs"

if exist "%BASE_DIR%\logs\batch.jsonl" (
  start "" notepad "%BASE_DIR%\logs\batch.jsonl"
) else (
  echo.
  echo No log yet: "%BASE_DIR%\logs\batch.jsonl"
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

if exist "%BASE_DIR%\pause.flag" del /f /q "%BASE_DIR%\pause.flag"
if exist "%BASE_DIR%\stop.flag" del /f /q "%BASE_DIR%\stop.flag"

echo.
echo Pause and stop flags have been reset.
pause
goto MENU


:DETECT_POWERSHELL
set "POWERSHELL_EXE="
where pwsh.exe >nul 2>nul
if errorlevel 1 goto DETECT_WINDOWS_POWERSHELL
set "POWERSHELL_EXE=pwsh.exe"
exit /b 0

:DETECT_WINDOWS_POWERSHELL
where powershell.exe >nul 2>nul
if errorlevel 1 exit /b 1
set "POWERSHELL_EXE=powershell.exe"
exit /b 0


:DETECT_PYTHON
set "PYTHON_EXE="
if exist "%SCRIPT_DIR%.venv\Scripts\python.exe" goto DETECT_VENV
goto DETECT_SYSTEM_PYTHON

:DETECT_VENV
set "PYTHON_EXE=%SCRIPT_DIR%.venv\Scripts\python.exe"
"%PYTHON_EXE%" -c "import clean_auto.pipeline" >nul 2>nul
if not errorlevel 1 exit /b 0
set "PYTHON_EXE="

:DETECT_SYSTEM_PYTHON
where python.exe >nul 2>nul
if errorlevel 1 exit /b 1
set "PYTHON_EXE=python.exe"
python.exe -c "import clean_auto.pipeline" >nul 2>nul
if errorlevel 1 (
  set "PYTHON_EXE="
  exit /b 1
)
exit /b 0


:CREATE_SELECTION
set "SELECTION_FILE="

if not defined POWERSHELL_EXE (
  echo.
  echo 当前系统未检测到 PowerShell，无法打开选择器；可使用命令行 --selection-file。
  pause
  exit /b 1
)

if not exist "%SELECTOR_SCRIPT%" (
  echo.
  echo [ERROR] Selector script not found: "%SELECTOR_SCRIPT%"
  pause
  exit /b 1
)

if not exist "%BASE_DIR%\input" mkdir "%BASE_DIR%\input"
if not exist "%BASE_DIR%\logs\selections" mkdir "%BASE_DIR%\logs\selections"

:NEW_SELECTION_NAME
set "SELECTION_FILE=%BASE_DIR%\logs\selections\menu-%RANDOM%-%RANDOM%-%RANDOM%.json"
if exist "%SELECTION_FILE%" goto NEW_SELECTION_NAME

"%POWERSHELL_EXE%" -NoProfile -STA -ExecutionPolicy Bypass -File "%SELECTOR_SCRIPT%" -InputRoot "%BASE_DIR%\input" -OutputPath "%SELECTION_FILE%" -Mode "%~1"
set "SELECT_ERR=%ERRORLEVEL%"

if "%SELECT_ERR%"=="2" (
  echo.
  echo 未选择文件，未启动任务。
  pause
  exit /b 1
)

if not "%SELECT_ERR%"=="0" (
  echo.
  echo [ERROR] Selection failed. Exit code: %SELECT_ERR%
  pause
  exit /b 1
)

if not exist "%SELECTION_FILE%" (
  echo.
  echo [ERROR] Selector did not create a selection file.
  pause
  exit /b 1
)

echo.
echo Selection saved for audit: "%SELECTION_FILE%"
exit /b 0


:OPEN_DIRECTORY
if not exist "%~1" mkdir "%~1"

if not exist "%~1" (
  echo.
  echo [ERROR] Could not create directory: "%~1"
  pause
  exit /b 1
)

start "" "%~1"
exit /b 0


:CHECK_RUN_FLAGS
if exist "%BASE_DIR%\stop.flag" (
  echo.
  echo [BLOCKED] stop.flag still exists.
  echo The cleaner would stop immediately after startup.
  echo Use menu option 7 after the previous process has ended.
  pause
  exit /b 1
)

if exist "%BASE_DIR%\pause.flag" (
  echo.
  echo [BLOCKED] pause.flag still exists.
  echo Use menu option 7, or run Continue.bat first.
  pause
  exit /b 1
)

exit /b 0


:RUN_CMD
echo.
echo [RUN] python -m clean_auto --base-dir "%BASE_DIR%" %*
echo.

"%PYTHON_EXE%" -m clean_auto --base-dir "%BASE_DIR%" %*

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


:END_ERROR
endlocal
exit /b 1
