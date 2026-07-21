@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist "pause.flag" (
  del /f /q "pause.flag"

  if exist "pause.flag" (
    echo Failed to remove pause.flag.
    pause
    exit /b 1
  )

  echo Pause cleared. Processing may continue.
) else (
  echo No pause request exists.
)

if exist "stop.flag" (
  echo.
  echo Warning: stop.flag still exists.
  echo The stop request was not removed.
  echo Wait for the cleaner to stop, then use menu option 7
  echo before starting a new run.
)

pause
exit /b 0
