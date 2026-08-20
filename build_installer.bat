@echo off
setlocal enabledelayedexpansion
REM ============================================================================
REM  build_installer.bat  --  build the CLASSIC Windows installer for VoxKey.
REM
REM  Robust against the most common Windows problem: the "python" command being
REM  a Microsoft Store stub that isn't real Python. This script finds real
REM  Python wherever it is (py launcher, PATH, common install dirs), and if it
REM  genuinely can't find any, it downloads and installs Python automatically.
REM
REM  Output: dist\VoxKey-Setup.exe  (a classic Windows Setup wizard)
REM  Just double-click this file. Nothing needs to be pre-configured.
REM ============================================================================

echo.
echo ================================================
echo   VoxKey - classic installer build
echo ================================================
echo.

REM ------------------------------------------------------------------ find python
set "PY="

REM 1) the py launcher is the most reliable on Windows
where py >nul 2>&1
if !errorlevel! == 0 (
  py -3 -c "import sys" >nul 2>&1
  if !errorlevel! == 0 set "PY=py -3"
)

REM 2) a real python.exe on PATH (must NOT be the Store stub).
REM    The Store stub lives under WindowsApps and exits without doing anything.
if not defined PY (
  for /f "delims=" %%i in ('where python 2^>nul') do (
    echo %%i | find /i "WindowsApps" >nul
    if !errorlevel! neq 0 (
      "%%i" -c "import sys" >nul 2>&1
      if !errorlevel! == 0 (
        set "PY=%%i"
        goto :py_found
      )
    )
  )
)
:py_found

REM 3) common install locations
if not defined PY (
  for %%D in (
    "%LocalAppData%\Programs\Python\Python313\python.exe"
    "%LocalAppData%\Programs\Python\Python312\python.exe"
    "%LocalAppData%\Programs\Python\Python311\python.exe"
    "%LocalAppData%\Programs\Python\Python310\python.exe"
    "C:\Python313\python.exe"
    "C:\Python312\python.exe"
    "C:\Python311\python.exe"
    "C:\Program Files\Python313\python.exe"
    "C:\Program Files\Python312\python.exe"
    "C:\Program Files\Python311\python.exe"
  ) do if exist %%D set "PY=%%D"
)

REM 4) still nothing -> download + install Python silently
if not defined PY (
  echo Python was not found on this system.
  echo Downloading the official Python installer...
  set "PYDL=%TEMP%\python-installer.exe"
  powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe' -OutFile '%TEMP%\python-installer.exe' -UseBasicParsing; exit 0 } catch { exit 1 }"
  if !errorlevel! neq 0 (
    echo.
    echo   Could not download Python automatically. Please install it from
    echo   https://www.python.org/downloads/ ^(tick "Add python.exe to PATH"^)
    echo   then run this file again.
    pause & exit /b 1
  )
  echo Installing Python ^(this takes a minute^)...
  "%TEMP%\python-installer.exe" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 Include_test=0
  REM give the installer a moment and re-detect
  timeout /t 5 /nobreak >nul
  set "PY=%LocalAppData%\Programs\Python\Python312\python.exe"
  if not exist "!PY!" set "PY=py -3"
)

echo Using Python: !PY!
!PY! --version
if !errorlevel! neq 0 (
  echo Python still not usable. Aborting.
  pause & exit /b 1
)
echo.

REM ------------------------------------------------------------------ build venv
echo [1/5] Creating build environment...
if not exist .buildenv (
  !PY! -m venv .buildenv
  if !errorlevel! neq 0 ( echo Failed to create venv. & pause & exit /b 1 )
)
set "VPY=.buildenv\Scripts\python.exe"
"!VPY!" -m pip install --upgrade pip >nul 2>&1

echo [2/5] Installing dependencies + PyInstaller...
"!VPY!" -m pip install -r requirements.txt
if !errorlevel! neq 0 ( echo Dependency install failed. & pause & exit /b 1 )
"!VPY!" -m pip install pyinstaller pillow
if !errorlevel! neq 0 ( echo PyInstaller install failed. & pause & exit /b 1 )

echo [3/5] Generating branding assets...
"!VPY!" installer\make_assets.py

echo [4/5] Building VoxKey.exe (bundled runtime, users need nothing)...
"!VPY!" -m PyInstaller VoxKey.spec --noconfirm
if !errorlevel! neq 0 ( echo App build failed. & pause & exit /b 1 )

REM ------------------------------------------------------------------ inno setup
echo [5/5] Building the classic installer...
set "ISCC="
for %%P in (
  "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
  "%ProgramFiles%\Inno Setup 6\ISCC.exe"
) do if exist %%~P set "ISCC=%%~P"

if not defined ISCC (
  echo   Inno Setup not found - downloading it...
  powershell -NoProfile -Command "try { Invoke-WebRequest -Uri 'https://files.jrsoftware.org/is/6/innosetup-6.3.3.exe' -OutFile '%TEMP%\innosetup.exe' -UseBasicParsing; exit 0 } catch { exit 1 }"
  if !errorlevel! neq 0 (
    echo.
    echo   Could not download Inno Setup. The app itself still built OK at:
    echo     dist\VoxKey\VoxKey.exe
    echo   Install Inno Setup from https://jrsoftware.org/isdl.php and re-run
    echo   to get the classic installer.
    pause & exit /b 1
  )
  "%TEMP%\innosetup.exe" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART
  for %%P in (
    "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
    "%ProgramFiles%\Inno Setup 6\ISCC.exe"
  ) do if exist %%~P set "ISCC=%%~P"
)

if not defined ISCC (
  echo   Inno Setup still not found. App built at dist\VoxKey\VoxKey.exe though.
  pause & exit /b 1
)

"!ISCC!" installer\VoxKey.iss
if !errorlevel! neq 0 ( echo Installer compile failed. & pause & exit /b 1 )

echo.
echo ================================================
echo   SUCCESS
echo ================================================
echo   Classic installer:  dist\VoxKey-Setup.exe
echo   (double-click it to run the Setup wizard)
echo.
echo   Standalone app also at: dist\VoxKey\VoxKey.exe
echo ================================================
echo.
pause
