# -*- mode: python ; coding: utf-8 -*-
# Three executables, TWO SEPARATE BUNDLES.
#
#   dist/VoxKey/                 VoxKey.exe (watchdog) + VoxKey-App.exe (the UI)
#   dist/VoxKey-Whisper/         VoxKey-Whisper.exe (the speech engine)
#
# WHY TWO BUNDLES — this is the fix for the 0xC0000005 access violation.
# A single COLLECT merged the Qt bundle and the ctranslate2 bundle into one
# folder. Both ship their own copies of the same DLL names (msvcp140.dll,
# vcruntime140.dll, zlib, libcrypto, the OpenMP runtime). When PyInstaller
# merges them, duplicates collapse to ONE copy — so the speech worker was
# loading Qt's build of libraries it was never linked against, and crashed
# during ctranslate2's native initialisation every single time, regardless of
# model size or compute type.
#
# Keeping the worker in its own folder gives it its own complete, consistent
# set of DLLs. The build then copies dist/VoxKey-Whisper into
# dist/VoxKey/whisper/ so the installer ships one tree.
#
# The worker also EXCLUDES PyQt6, sounddevice and pynput outright: it never
# touches a GUI or a microphone, it only turns a .npy file into text. Every
# library left out is one that can't collide.

from PyInstaller.utils.hooks import collect_all, collect_submodules

pyqt_datas, pyqt_binaries, pyqt_hidden = collect_all('PyQt6')
ct_datas, ct_binaries, ct_hidden = collect_all('ctranslate2')
fw_datas, fw_binaries, fw_hidden = collect_all('faster_whisper')
sd_datas, sd_binaries, sd_hidden = collect_all('sounddevice')

# tokenizers + huggingface_hub are pulled in by faster_whisper and carry
# native extensions of their own; collect them explicitly so the worker is
# self-contained.
try:
    tk_datas, tk_binaries, tk_hidden = collect_all('tokenizers')
except Exception:
    tk_datas, tk_binaries, tk_hidden = [], [], []
try:
    hf_datas, hf_binaries, hf_hidden = collect_all('huggingface_hub')
except Exception:
    hf_datas, hf_binaries, hf_hidden = [], [], []
try:
    ov_datas, ov_binaries, ov_hidden = collect_all('onnxruntime')
except Exception:
    ov_datas, ov_binaries, ov_hidden = [], [], []

block_cipher = None

import os as _os
_icon = 'whispr/assets/voxkey.ico'
if not _os.path.exists(_icon):
    _icon = None

# ------------------------------------------------------------------ APP
# The UI needs Qt, the mic and the keyboard — but NOT the speech engine,
# which now lives entirely in the worker process.
app_hidden = (pyqt_hidden + sd_hidden +
              collect_submodules('pynput') +
              ['pynput.keyboard._win32', 'pynput.mouse._win32'])
app_binaries = pyqt_binaries + sd_binaries
app_datas = ([('whispr/assets', 'whispr/assets'),
              # ship the worker source too: it lets VoxKey fall back to a
              # system Python install if the frozen worker ever fails.
              ('whispr/core/transcribe_worker.py', 'whispr/core')] +
             pyqt_datas + sd_datas)

app_a = Analysis(
    ['whispr/__main__.py'],
    pathex=[], binaries=app_binaries, datas=app_datas,
    hiddenimports=app_hidden, hookspath=[], hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pytest', 'ctranslate2',
              'faster_whisper', 'torch'],
    cipher=block_cipher, noarchive=False,
)
app_pyz = PYZ(app_a.pure, app_a.zipped_data, cipher=block_cipher)
app_exe = EXE(
    app_pyz, app_a.scripts, [],
    exclude_binaries=True, name='VoxKey-App',
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, console=False, icon=_icon,
)

# ------------------------------------------------------------- WATCHDOG
wd_a = Analysis(
    ['whispr/watchdog.py'],
    pathex=[], binaries=pyqt_binaries,
    datas=[('whispr/assets', 'whispr/assets')] + pyqt_datas,
    hiddenimports=pyqt_hidden, hookspath=[], hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pytest', 'ctranslate2',
              'faster_whisper', 'torch'],
    cipher=block_cipher, noarchive=False,
)
wd_pyz = PYZ(wd_a.pure, wd_a.zipped_data, cipher=block_cipher)
wd_exe = EXE(
    wd_pyz, wd_a.scripts, [],
    exclude_binaries=True, name='VoxKey',
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, console=False, icon=_icon,
)

# App + watchdog share Qt happily — same library, same build.
coll = COLLECT(
    app_exe, app_a.binaries, app_a.zipfiles, app_a.datas,
    wd_exe, wd_a.binaries, wd_a.zipfiles, wd_a.datas,
    strip=False, upx=False, upx_exclude=[], name='VoxKey',
)

# --------------------------------------------------------- SPEECH WORKER
# Its own bundle, its own DLLs, no Qt anywhere near it.
wk_hidden = (ct_hidden + fw_hidden + tk_hidden + hf_hidden + ov_hidden)
wk_binaries = (ct_binaries + fw_binaries + tk_binaries + hf_binaries +
               ov_binaries)
wk_datas = ct_datas + fw_datas + tk_datas + hf_datas + ov_datas

wk_a = Analysis(
    ['whispr/core/whisper_main.py'],
    pathex=[], binaries=wk_binaries, datas=wk_datas,
    hiddenimports=wk_hidden, hookspath=[], hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'pytest',
              'PyQt6', 'PyQt5', 'PySide6', 'sounddevice', 'pynput',
              'pyperclip', 'torch'],
    cipher=block_cipher, noarchive=False,
)
wk_pyz = PYZ(wk_a.pure, wk_a.zipped_data, cipher=block_cipher)
wk_exe = EXE(
    wk_pyz, wk_a.scripts, [],
    exclude_binaries=True, name='VoxKey-Whisper',
    debug=False, bootloader_ignore_signals=False, strip=False,
    upx=False, console=False, icon=_icon,
)
wk_coll = COLLECT(
    wk_exe, wk_a.binaries, wk_a.zipfiles, wk_a.datas,
    strip=False, upx=False, upx_exclude=[], name='VoxKey-Whisper',
)


# ---------------------------------------------------------------- POST-BUILD
# Nest the worker bundle inside the app folder, HERE rather than in the
# GitHub workflow file. Two reasons:
#   * a .spec file is just Python, so this runs as part of every build,
#     however it's launched — CI, build.bat, or by hand;
#   * it means .github/workflows/build.yml never has to change, so pushing
#     this repo only needs a token with 'repo' scope, not 'workflow'.
import os as _os2
import shutil as _shutil
import subprocess as _subprocess

_dist = _os2.path.join(DISTPATH)
_app = _os2.path.join(_dist, 'VoxKey')
_wk = _os2.path.join(_dist, 'VoxKey-Whisper')
_target = _os2.path.join(_app, 'whisper')

print('=' * 62)
print('POST-BUILD: nesting the speech worker inside the app folder')
print('=' * 62)

if _os2.path.isdir(_wk) and _os2.path.isdir(_app):
    if _os2.path.isdir(_target):
        _shutil.rmtree(_target, ignore_errors=True)
    _shutil.copytree(_wk, _target)
    _exe = _os2.path.join(_target, 'VoxKey-Whisper.exe')
    if _os2.path.exists(_exe):
        print('OK  worker nested at %s' % _target)
        print('    size: %s bytes' % format(_os2.path.getsize(_exe), ','))

        # Smoke-test it. The 0xC0000005 DLL-collision crash was invisible at
        # build time and only showed up on a user's machine; this makes the
        # build itself notice.
        try:
            _p = _subprocess.Popen([_exe, 'tiny', 'en', '', 'int8'],
                                   stdin=_subprocess.PIPE,
                                   stdout=_subprocess.PIPE,
                                   stderr=_subprocess.PIPE)
            import time as _time
            _time.sleep(20)
            if _p.poll() is None:
                print('OK  worker still running after 20s (no native crash)')
                _p.kill()
            else:
                _code = _p.returncode & 0xFFFFFFFF
                print('!!  WARNING: worker exited with 0x%08X' % _code)
                if _code == 0xC0000005:
                    print('!!  ACCESS VIOLATION — the DLL collision is back.')
                    print('!!  The worker bundle must stay separate from Qt.')
        except Exception as _e:
            print('..  smoke test could not run: %s' % _e)
    else:
        raise SystemExit('POST-BUILD FAILED: VoxKey-Whisper.exe missing after copy')
else:
    print('..  skipped (expected %s and %s)' % (_app, _wk))
print('=' * 62)
