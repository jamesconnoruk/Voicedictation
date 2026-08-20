@echo off
REM ---------------------------------------------------------------------
REM Run VoxKey directly from source, using your own Python 3.12.
REM
REM No PyInstaller, no GitHub build, no installer. This bypasses the
REM broken bundled worker entirely: your Python already has a working
REM faster-whisper, and running from source uses it directly.
REM
REM Close the installed VoxKey first (tray icon -> Quit) so the two
REM don't fight over the hotkey.
REM ---------------------------------------------------------------------
title VoxKey (from source)
cd /d "%~dp0"

set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python

echo Using: %PY%
"%PY%" --version || (echo Python not found. & pause & exit /b 1)

echo.
echo Installing the UI dependencies (one time, ~1 minute)...
"%PY%" -m pip install --quiet PyQt6 sounddevice pynput pyperclip numpy faster-whisper

echo.
echo Starting VoxKey. Look for the tray icon.
echo Hold your hotkey and speak. Close this window to quit.
echo.
"%PY%" -m whispr
pause
