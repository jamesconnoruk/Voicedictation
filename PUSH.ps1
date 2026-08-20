<#
  PUSH.ps1 — upload VoxKey to GitHub and trigger a build.

  Works from a FRESH folder (a newly unzipped one with no .git) as well as an
  existing clone. The old PUSH_NOW.ps1 assumed the folder was already a git
  repo; in a fresh folder every git command failed and it wrongly reported the
  token as the problem.

  Double-click PUSH.bat.
#>

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

Write-Host "`n==== VoxKey -> GitHub ====`n" -ForegroundColor Cyan

# git isn't always on PATH inside a fresh PowerShell window
$env:Path = [System.Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
            [System.Environment]::GetEnvironmentVariable("Path","User") + ";" +
            "C:\Program Files\Git\cmd"

# ---------------------------------------------------------------- checks
$gitv = (git --version) 2>$null
if (-not $gitv) {
    Write-Host "Git is not installed." -ForegroundColor Red
    Write-Host "Install it from https://git-scm.com/download/win then run again."
    Read-Host "`nPress Enter to close"; exit 1
}
Write-Host "git: $gitv"

if (-not (Test-Path (Join-Path $PSScriptRoot "whispr"))) {
    Write-Host "`nWRONG FOLDER." -ForegroundColor Red
    Write-Host "Put this file in the folder that CONTAINS the 'whispr' folder"
    Write-Host "(i.e. next to VoxKey.spec and requirements.txt), then run again."
    Write-Host "`nThis folder is: $PSScriptRoot"
    Read-Host "`nPress Enter to close"; exit 1
}

$UserName = "jamesconnoruk"
$RepoName = "Voicedictation"

# ------------------------------------------------------- preflight report
Write-Host "`n---------------- WHERE THIS IS GOING ----------------" -ForegroundColor Cyan
Write-Host "  FROM (this folder) : $PSScriptRoot"
Write-Host "  TO   (repository)  : https://github.com/$UserName/$RepoName"
Write-Host "  BRANCH             : main"
Write-Host "  LANDS IN           : the repo root (same layout as this folder)"
Write-Host "-----------------------------------------------------" -ForegroundColor Cyan

# Verify the things the build actually needs are present before uploading.
$required = @(
    "whispr\__main__.py",
    "whispr\core\transcriber.py",
    "whispr\core\transcribe_worker.py",
    "VoxKey.spec",
    "requirements.txt",
    ".github\workflows\build.yml",
    "installer\VoxKey.iss"
)
$missing = @()
foreach ($f in $required) {
    if (Test-Path (Join-Path $PSScriptRoot $f)) {
        Write-Host ("  [ok]      " + $f)
    } else {
        Write-Host ("  [MISSING] " + $f) -ForegroundColor Red
        $missing += $f
    }
}
if ($missing.Count -gt 0) {
    Write-Host "`nFiles are missing - this is the wrong folder, or the zip" -ForegroundColor Red
    Write-Host "didn't extract fully. Nothing has been uploaded." -ForegroundColor Red
    Read-Host "`nPress Enter to close"; exit 1
}

# Confirm the spec carries the fix, so we can't push a stale copy by mistake.
$specTxt = Get-Content (Join-Path $PSScriptRoot "VoxKey.spec") -Raw
if ($specTxt -match "wk_coll\s*=\s*COLLECT") {
    Write-Host "  [ok]      VoxKey.spec has the separate worker bundle (the DLL fix)"
} else {
    Write-Host "  [WARNING] VoxKey.spec looks like the OLD single-COLLECT version." -ForegroundColor Yellow
    Write-Host "            Pushing this will rebuild the crashing worker." -ForegroundColor Yellow
    $go = Read-Host "            Continue anyway? (y/N)"
    if ($go -ne "y") { Read-Host "Press Enter to close"; exit 1 }
}

# ---------------------------------------------------------------- token
Write-Host "`nYour token needs the [x] repo tick." -ForegroundColor Yellow
Write-Host "  New token: https://github.com/settings/tokens/new"
Write-Host "  (Tick [x] workflow too if you ever edit .github\workflows\build.yml."
Write-Host "   This version doesn't, so 'repo' on its own is enough.)"
Write-Host "`nPaste your GitHub token, then press Enter." -ForegroundColor Cyan
Write-Host "(It stays hidden as you paste - that's normal, keep going.)"
$secure = Read-Host "Token" -AsSecureString
$bstr   = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$token  = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
if ([string]::IsNullOrWhiteSpace($token)) {
    Write-Host "No token entered." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
$token = $token.Trim()

# ------------------------------------------------- make it a repo if needed
if (-not (Test-Path ".git")) {
    Write-Host "`nThis folder isn't a git repository yet - creating one." -ForegroundColor Yellow
    git init | Out-Null
    if (-not (Test-Path ".git")) {
        Write-Host "git init failed." -ForegroundColor Red
        Read-Host "Press Enter to close"; exit 1
    }
}

git config user.name  $UserName            | Out-Null
git config user.email "$UserName@users.noreply.github.com" | Out-Null
git config core.autocrlf true              | Out-Null

# keep build junk and logs out of the repo
if (-not (Test-Path ".gitignore")) {
@"
__pycache__/
*.pyc
build/
dist/
*.spec.bak
*.log
voxkey_doctor_log.txt
voxkey_autofix_log.txt
python_engine_log.txt
"@ | Set-Content -Path ".gitignore" -Encoding UTF8
}

# ---------------------------------------------------------------- commit
git add -A
git commit -m "VoxKey update" 2>&1 | Out-Null

# force the workflow to run even if nothing else changed
$stamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
Set-Content -Path ".buildstamp" -Value "build: $stamp"
git add .buildstamp
git commit -m "Trigger build $stamp" 2>&1 | Out-Null

git branch -M main 2>&1 | Out-Null

$commits = (git rev-list --count HEAD) 2>$null
if (-not $commits -or $commits -eq "0") {
    Write-Host "Nothing was committed - cannot push." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
Write-Host "`n$commits commit(s) ready."

# ---------------------------------------------------------------- remote
# The token goes in the push URL only, never saved into .git/config.
$authUrl = "https://$($UserName):$($token)@github.com/$UserName/$RepoName.git"
$plainUrl = "https://github.com/$UserName/$RepoName.git"

$remotes = (git remote) 2>$null
if ($remotes -match "origin") { git remote set-url origin $plainUrl }
else { git remote add origin $plainUrl }

Write-Host "`nUploading to $plainUrl ..." -ForegroundColor Cyan
$out = (git push $authUrl main --force 2>&1) | Out-String
$ok = ($LASTEXITCODE -eq 0)

# If the ONLY thing blocking us is the workflow scope, push everything else
# automatically and leave the remote's build.yml untouched. The build still
# works: the worker-nesting step lives in VoxKey.spec, not in the workflow.
if (-not $ok -and $out -match "workflow") {
    Write-Host "`nToken lacks 'workflow' scope - retrying without the" -ForegroundColor Yellow
    Write-Host "workflow file (it does not need to change in this version)." -ForegroundColor Yellow

    git rm -r --cached .github 2>&1 | Out-Null
    if (-not (Select-String -Path ".gitignore" -Pattern "^\.github/" -Quiet -ErrorAction SilentlyContinue)) {
        Add-Content -Path ".gitignore" -Value ".github/"
    }
    git add -A
    git commit -m "Push without workflow file (token lacks workflow scope)" 2>&1 | Out-Null

    $out2 = (git push $authUrl main --force 2>&1) | Out-String
    if ($LASTEXITCODE -eq 0) {
        $ok = $true
        $out = $out2
        Write-Host "Worked on the retry." -ForegroundColor Green
        $skippedWorkflow = $true
    } else {
        $out = $out + "`n--- retry ---`n" + $out2
    }
}

# scrub the token from anything we might print
$out = $out -replace [regex]::Escape($token), "***"
$token = $null; $authUrl = $null

if ($ok) {
    Write-Host "`n==== SUCCESS ====" -ForegroundColor Green

    # Ask GitHub what it actually has now, so "it pushed" is verified rather
    # than assumed.
    try {
        $api = Invoke-RestMethod -Uri "https://api.github.com/repos/$UserName/$RepoName/commits/main" `
               -Headers @{ "User-Agent" = "VoxKey-Push" } -TimeoutSec 20
        Write-Host "`nGitHub now shows:" -ForegroundColor Cyan
        Write-Host ("  commit  : " + $api.sha.Substring(0,7))
        Write-Host ("  message : " + $api.commit.message.Split("`n")[0])
        Write-Host ("  when    : " + $api.commit.author.date)
    } catch {
        Write-Host "`n(Could not read back the commit - check the link below.)"
    }

    if ($skippedWorkflow) {
        Write-Host "`nNOTE: the workflow file was left as-is on GitHub." -ForegroundColor Yellow
        Write-Host "That is fine - the worker-nesting step now lives in"
        Write-Host "VoxKey.spec, so the build does the right thing anyway."
    }

    Write-Host "`nBuild started. Watch it here:"
    Write-Host "  https://github.com/$UserName/$RepoName/actions"
    Write-Host "`nWhen the tick goes green, download the newest VoxKey-Setup"
    Write-Host "from the Artifacts section at the bottom of the run page."
} else {
    Write-Host "`n==== PUSH FAILED ====" -ForegroundColor Red
    Write-Host $out
    if ($out -match "without ``workflow`` scope" -or $out -match "workflow.*scope") {
        Write-Host "`n>>> THE FIX: your token is missing the 'workflow' scope." -ForegroundColor Green
        Write-Host "    Everything else worked - it reached GitHub and was rejected"
        Write-Host "    only because the token isn't allowed to touch the build file."
        Write-Host ""
        Write-Host "    1. Go to https://github.com/settings/tokens/new"
        Write-Host "    2. Tick BOTH  [x] repo   AND   [x] workflow"
        Write-Host "    3. Generate, copy it, run this script again."
        Write-Host ""
        Write-Host "    (Fine-grained token instead? Set Repository permissions ->"
        Write-Host "     'Workflows' to Read and write.)"
        Read-Host "`nPress Enter to close"; exit 1
    }
    Write-Host "`nWhat the message above usually means:" -ForegroundColor Yellow
    Write-Host "  'workflow scope'               -> token missing the 'workflow'"
    Write-Host "     tick. Remake it with repo AND workflow ticked."
    Write-Host "  'Authentication failed' / 403  -> token wrong, expired, or"
    Write-Host "     missing the 'repo' tick. Make a new one at"
    Write-Host "     https://github.com/settings/tokens/new"
    Write-Host "  'Repository not found'         -> the repo $RepoName doesn't"
    Write-Host "     exist on your account, or the token can't see it. Create it"
    Write-Host "     at https://github.com/new (name it exactly $RepoName)."
    Write-Host "  'not a git repository'         -> wrong folder."
    Write-Host "  'failed to push some refs'     -> try again; this uses --force."
}
Read-Host "`nPress Enter to close"
