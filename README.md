# VoxKey — local push-to-talk dictation (a Wispr-style clone)

Hold a hotkey, speak, release — your words are transcribed **locally** (no cloud,
no account, no internet) and pasted at your cursor. A black waveform pill shows
live mic levels while you hold the keys. Every dictation is saved as its own
transcript you can search and **correct** — and your corrections make future
recognition better.

## What it does

- **Push-to-talk hotkey** (default `Ctrl+Shift`, configurable). Hold = record,
  release = transcribe + paste. Nothing happens otherwise — it just sits in the
  tray.
- **Live waveform overlay** — a black rounded pill at the bottom-centre of the
  screen, showing real-time mic input, visible only while you hold the keys.
- **Local speech-to-text** via `faster-whisper` (runs on CPU; much faster on an
  NVIDIA GPU). Model size is selectable in Settings.
- **Transcript library** — each dictation is a separate, timestamped card,
  labelled with the app you pasted into. Searchable.
- **Correction learning** — right-click a transcript (or hit *Correct*) and fix
  a word. VoxKey (a) auto-applies that fix to future transcripts, and (b) adds
  the word to a custom vocabulary that biases Whisper so it gets it right at the
  source. Fuzzy-matched so near-misses are caught too.
- **Voice Setup / trainer** — read a few sentences to calibrate silence
  threshold + mic gain to your voice, mic and room; add names/jargon to the
  custom vocabulary.
- **Runs at login** (optional) and lives in the system tray.

## About "training on your voice"

Modern local STT (Whisper) is already speaker-independent and handles accents
well, and you can't meaningfully fine-tune the neural net from a handful of
sentences on a normal PC. So VoxKey does the two things that *actually* improve
your personal accuracy, which is what the "voice trainer" performs:

1. **Calibration** — tunes VAD/levels to your mic + voice so it doesn't clip you
   or paste empty text.
2. **Custom vocabulary + correction learning** — makes your names, jargon and
   commonly-misheard words come out right, and keeps improving as you correct.

This is the same approach commercial "custom vocabulary" features use, and it's
the highest-leverage thing you can do locally.

## Install & run (from source)

```bash
python -m venv .venv
.venv\Scripts\activate           # Windows
pip install -r requirements.txt
python -m whispr                 # starts in the system tray
```

First launch downloads the Whisper model (once). Open the tray icon → **Voice
Setup** → calibrate and add vocabulary. Then hold `Ctrl+Shift` anywhere and
speak.

### GPU (optional, much faster)
Install an NVIDIA CUDA 12 runtime + cuDNN and set **device: cuda** behaviour is
automatic (`device="auto"` picks CUDA if available). CPU works fine with the
`small` model for everyday dictation.

## British / UK English

VoxKey ships with British English on by default. There's no separate "en-GB"
Whisper model, so this works in two layers: an accent-context prompt nudges the
decoder toward British spelling, and a deterministic US→UK normaliser fixes the
output (colour, favourite, organise, centre, travelling, analyse, …). It
preserves capitalisation and deliberately leaves context-risky words alone
(e.g. "check the box" isn't turned into "cheque"). Toggle it in Settings.

## Branding

The app, tray icon, installer, and window header all use the VOX Voice Zone
logo (`whispr/assets/logo_full.png`). The theme is black + white to match it.
`installer/make_assets.py` regenerates the app icon (`voxkey.ico`), the wizard
banner bitmaps, and the licence file from that logo — so if you ever swap the
logo, just re-run the build and everything updates.

## Build the installer — one step

Double-click **`build.bat`** (or run `build.ps1`). That's it.

It runs a PowerShell builder that is deliberately robust:

- **Finds Python wherever it actually is** — the `py` launcher, a real
  `python` on PATH (ignoring the Microsoft Store stub that causes the common
  "Python was not found" error), or common install folders.
- **Installs Python automatically** if none is found (downloads the official
  python.org installer and runs it silently).
- Creates an isolated build environment and installs all dependencies.
- Generates the VOX-branded icon and wizard art.
- Builds `dist\VoxKey\VoxKey.exe` with PyInstaller (bundles Python + Qt + the
  speech runtime, so **end users need nothing installed**).
- **Downloads and installs Inno Setup automatically** if it isn't present, then
  compiles the classic Windows installer.

Output:

- `dist\VoxKey-Setup.exe` — the classic Windows Setup wizard: welcome page with
  the VOX banner, licence page, choose-install-location, component checkboxes
  (desktop shortcut, launch-at-login), a real progress bar, Start-Menu group,
  an uninstaller, and it launches VoxKey when Setup finishes.
- `dist\VoxKey\VoxKey.exe` — the standalone app (also runnable directly).

### If PowerShell is blocked on your machine

Some locked-down machines disable script execution. Run this once in an admin
PowerShell, then double-click `build.bat` again:

```
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

Or run the builder directly, which bypasses the policy for just that run:

```
powershell -ExecutionPolicy Bypass -File build.ps1
```

### Why the build can't be done for you

A Windows `.exe` can only be produced on Windows — PyInstaller and Inno Setup
build for the OS they run on, so they can't be cross-compiled from Linux. The
build was validated on the developer side by packaging the app end-to-end and
confirming the bundled executable launches; on Windows the same steps yield the
`.exe`.

## Run the tests (the "simulation")

```bash
pip install pytest
python -m pytest tests/test_all.py -v
```

These exercise the full push-to-talk pipeline with fakes standing in for the
mic, model and GUI: hotkey hold/release edges, VAD, silence rejection, the
transcribe→correct→paste→save flow, correction learning, and calibration maths.

## Architecture

```
whispr/
  core/
    config.py        settings (JSON, per-user)
    hotkey.py        hold-to-talk detection (pure matcher + pynput listener)
    recorder.py      mic capture + RMS levels + VAD/silence trimming (numpy)
    transcriber.py   faster-whisper wrapper (lazy-loaded, kept warm)
    corrections.py   learn-from-edits + custom-vocabulary biasing
    transcripts.py   per-dictation storage (JSON lines)
    output.py        paste at cursor (clipboard/type) + active window title
    controller.py    the push-to-talk STATE MACHINE tying it all together
    autostart.py     launch-at-login (Windows registry)
  ui/
    overlay.py       the black waveform pill
    main_window.py   transcript cards + right-click correction + settings
    voice_setup.py   calibration + vocabulary tab (+ pure calibration maths)
    tray.py          the background daemon
  __main__.py        entry point
```

Core logic is deliberately decoupled from hardware/GUI (deps imported lazily,
state machine takes injected recorder/transcriber/output), which is what makes
it testable and what let the simulation pass before it ever touched a mic.

## macOS later
The core is cross-platform already. To ship on Mac you'd swap the paste/active-
window bits (use the `Cmd` modifier and AppleScript/Accessibility APIs) and add
mic + accessibility permission prompts. `sounddevice`, `pynput`, `PyQt6` and
`faster-whisper` all run on macOS (CPU; no Metal acceleration for faster-whisper
yet — whisper.cpp is the faster Mac backend if you want it later).
```
