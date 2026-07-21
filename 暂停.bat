@echo off
setlocal EnableExtensions
cd /d "%~dp0"

if exist "stop.flag" (
  echo A stop request already exists.
  echo Pause was not requested.
  pause
  exit /b 1
)

if exist "pause.flag" (
  echo The cleaner is already paused or waiting to pause.
  pause
  exit /b 0
)

type nul > "pause.flag"

if errorlevel 1 (
  echo Failed to create pause.flag.
  pause
  exit /b 1
)

echo Pause requested.
echo The cleaner will pause at the next control checkpoint.
pause
exit /b 0
