<#
  build.ps1 — reliable end-to-end builder for VoxKey on Windows.

  Why PowerShell: it's present on every Windows machine, so it never hits the
  "python was not found" problem the way a bare .bat does. It finds real Python
  (or installs it), builds the app with PyInstaller, fetches Inno Setup if
  needed, and compiles the classic Setup.exe.

  Run it by double-clicking build.bat (which just launches this), or:
      powershell -ExecutionPolicy Bypass -File build.ps1
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

function Section($t) { Write-Host "`n==== $t ====" -ForegroundColor Cyan }

# --------------------------------------------------------------- find real python
function Find-Python {
    # 1) py launcher (most reliable on Windows)
    $py = Get-Command py -ErrorAction SilentlyContinue
    if ($py) {
        try { & py -3 -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { return "py -3" } } catch {}
    }
    # 2) python on PATH, but reject the Microsoft Store stub (lives in WindowsApps)
    foreach ($name in @("python","python3")) {
        $cmds = Get-Command $name -All -ErrorAction SilentlyContinue
        foreach ($c in $cmds) {
            $src = $c.Source
            if ($src -and ($src -notmatch "WindowsApps")) {
                try { & $src -c "import sys" 2>$null; if ($LASTEXITCODE -eq 0) { return "`"$src`"" } } catch {}
            }
        }
    }
    # 3) common install locations
    $candidates = @(
        "$env:LocalAppData\Programs\Python\Python313\python.exe",
        "$env:LocalAppData\Programs\Python\Python312\python.exe",
        "$env:LocalAppData\Programs\Python\Python311\python.exe",
        "$env:LocalAppData\Programs\Python\Python310\python.exe",
        "C:\Python313\python.exe","C:\Python312\python.exe","C:\Python311\python.exe",
        "C:\Program Files\Python313\python.exe","C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python311\python.exe"
    )
    foreach ($c in $candidates) { if (Test-Path $c) { return "`"$c`"" } }
    return $null
}

function Install-Python {
    Section "Installing Python (not found on this system)"
    $url = "https://www.python.org/ftp/python/3.12.7/python-3.12.7-amd64.exe"
    $out = "$env:TEMP\python-3.12.7-amd64.exe"
    Write-Host "Downloading $url ..."
    Invoke-WebRequest -Uri $url -OutFile $out -UseBasicParsing
    Write-Host "Running the Python installer silently ..."
    Start-Process -FilePath $out -ArgumentList `
        "/quiet","InstallAllUsers=0","PrependPath=1","Include_launcher=1","Include_test=0" `
        -Wait
    Start-Sleep -Seconds 4
}

Section "VoxKey build"

$PY = Find-Python
if (-not $PY) {
    Install-Python
    $PY = Find-Python
    if (-not $PY) { throw "Python still not found after install. Install manually from python.org and re-run." }
}
Write-Host "Using Python: $PY"
Invoke-Expression "$PY --version"

# --------------------------------------------------------------- venv + deps
Section "Creating build environment"
if (-not (Test-Path ".buildenv")) { Invoke-Expression "$PY -m venv .buildenv" }
$VPY = ".\.buildenv\Scripts\python.exe"

Section "Installing dependencies + PyInstaller"
& $VPY -m pip install --upgrade pip
& $VPY -m pip install -r requirements.txt
& $VPY -m pip install pyinstaller pillow

Section "Generating branding assets"
& $VPY installer\make_assets.py

Section "Building VoxKey.exe (bundled runtime)"
& $VPY -m PyInstaller VoxKey.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller build failed." }

# --------------------------------------------------------------- inno setup
Section "Building the classic installer (Inno Setup)"
$iscc = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $iscc) {
    Write-Host "Inno Setup not found - downloading ..."
    $innoUrl = "https://files.jrsoftware.org/is/6/innosetup-6.3.3.exe"
    $innoOut = "$env:TEMP\innosetup-6.3.3.exe"
    try {
        Invoke-WebRequest -Uri $innoUrl -OutFile $innoOut -UseBasicParsing
        Start-Process -FilePath $innoOut -ArgumentList "/VERYSILENT","/SUPPRESSMSGBOXES","/NORESTART" -Wait
        Start-Sleep -Seconds 3
        $iscc = @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
        ) | Where-Object { Test-Path $_ } | Select-Object -First 1
    } catch {
        Write-Host "Could not download Inno Setup." -ForegroundColor Yellow
    }
}

if ($iscc) {
    & $iscc installer\VoxKey.iss
    if ($LASTEXITCODE -ne 0) { throw "Installer compile failed." }
    Section "SUCCESS"
    Write-Host "Classic installer: dist\VoxKey-Setup.exe" -ForegroundColor Green
    Write-Host "Standalone app:    dist\VoxKey\VoxKey.exe" -ForegroundColor Green
} else {
    Section "App built, installer skipped"
    Write-Host "The app is ready at dist\VoxKey\VoxKey.exe" -ForegroundColor Green
    Write-Host "Install Inno Setup from https://jrsoftware.org/isdl.php to build the wizard." -ForegroundColor Yellow
}

Write-Host "`nDone. Press Enter to close."
Read-Host
