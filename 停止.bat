@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist "stop.flag" (
  echo A safe stop has already been requested.
  echo Wait for the cleaner process to exit.
  pause
  exit /b 0
)

type nul > "stop.flag"

if errorlevel 1 (
  echo Failed to create stop.flag.
  pause
  exit /b 1
)

echo Safe stop requested.
echo The cleaner will stop at the next control checkpoint.
echo A partial response may be saved if output was already received.
echo.
echo Wait for the cleaner process to exit.
echo Then use menu option 7 before starting a new run.
pause
exit /b 0
