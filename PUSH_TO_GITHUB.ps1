<#
  PUSH_TO_GITHUB.ps1  (v4 - hidden token, forces a rebuild)

  Uploads VoxKey to GitHub and triggers a fresh build.
  - The token is typed HIDDEN so it never appears on screen.
  - Forces a new build even if files haven't changed, so the workflow always runs.

  PUT THIS FILE inside your VoxKey project folder (the one with the 'whispr'
  folder), then double-click PUSH_TO_GITHUB.bat.
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

Write-Host "`n==== VoxKey -> GitHub ====`n" -ForegroundColor Cyan

$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User")

if (-not (Test-Path (Join-Path $PSScriptRoot "whispr"))) {
    Write-Host "WRONG FOLDER. Put this file in your VoxKey folder (the one that" -ForegroundColor Red
    Write-Host "contains the 'whispr' folder), then run again." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}

$UserName = "jamesconnoruk"

# --- token typed HIDDEN so it never shows on screen -----------------------
Write-Host "Paste your GitHub token, then press Enter." -ForegroundColor Cyan
Write-Host "(It stays hidden - you won't see it as you paste. That's normal.)"
$secure = Read-Host "Token" -AsSecureString
$bstr   = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$token  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
if ([string]::IsNullOrWhiteSpace($token)) { Write-Host "No token entered."; Read-Host "Press Enter"; exit 1 }

$authUrl = "https://$UserName`:$token@github.com/jamesconnoruk/Voicedictation.git"

git config --global user.name  $UserName 2>$null
git config --global user.email "$UserName@users.noreply.github.com" 2>$null

if (-not (Test-Path ".git")) { git init | Out-Null }
git add -A
git commit -m "Update VoxKey" 2>$null

# Force a build even if nothing changed, by writing a tiny changing file.
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Set-Content -Path ".buildstamp" -Value "build: $stamp"
git add .buildstamp
git commit -m "Trigger build $stamp" 2>$null

git branch -M main 2>$null
$remotes = git remote 2>$null
if ($remotes -match "origin") { git remote set-url origin $authUrl }
else { git remote add origin $authUrl }

Write-Host "`nUploading..." -ForegroundColor Cyan
git push -u origin main --force

$ok = ($LASTEXITCODE -eq 0)

# wipe the token from memory/vars
$token = $null; $authUrl = $null

if ($ok) {
    Write-Host "`n==== SUCCESS - uploaded ====" -ForegroundColor Green
    Write-Host "A NEW build has started. Watch it here:"
    Write-Host "  https://github.com/$UserName/Voicedictation/actions"
    Write-Host "Wait for the green tick, then download the newest VoxKey-Setup."
} else {
    Write-Host "`nPush failed - token likely wrong/expired or missing 'repo' scope." -ForegroundColor Yellow
    Write-Host "Make a new token at https://github.com/settings/tokens/new (tick repo) and run again."
}
Read-Host "`nPress Enter to close"
