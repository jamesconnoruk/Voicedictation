@echo off
REM Launches the reliable PowerShell builder. PowerShell is always present on
REM Windows, so this avoids the "python was not found" failure entirely.
echo Starting VoxKey build via PowerShell...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1"
if errorlevel 1 (
  echo.
  echo Build reported a problem. See the messages above.
  pause
)
