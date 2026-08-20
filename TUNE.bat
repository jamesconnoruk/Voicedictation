@echo off
REM Measures how fast each model really is on YOUR CPU and applies the best.
title VoxKey Tune
cd /d "%~dp0"
set PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe
if not exist "%PY%" set PY=python
"%PY%" -m pip install --quiet sounddevice numpy faster-whisper
"%PY%" voxkey_tune.py %*
pause
