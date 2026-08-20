r"""
watchdog.py — the XP-style crash catcher.

This is how Windows XP's error dialog actually worked: an OUTSIDE handler shows
the dialog, because the crashed program is already dead and can't draw anything
itself. We do the same — this watchdog is a separate process that:

  1. launches the real VoxKey app,
  2. waits for it to exit,
  3. if it exited with a CRASH code (native access violation 0xC0000005, a
     Python error, etc.), reads the crash/fault logs and pops up an
     XP-style dialog with a "Show log" button.

Because the watchdog is a separate, still-alive process, the dialog appears
even when VoxKey crashed hard at the C++/native level — exactly the case that
an in-process Python popup can never handle.

Built as VoxKey.exe (the thing users launch). It runs the real app as
VoxKey-App.exe under the hood.
"""
from __future__ import annotations
import os
import sys
import subprocess
from datetime import datetime


def log_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "VoxKey")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~")
    return d


LOGDIR = log_dir()
CRASH_LOG = os.path.join(LOGDIR, "voxkey_crash.log")
FAULT_LOG = os.path.join(LOGDIR, "voxkey_fault.log")
STDOUT_LOG = os.path.join(LOGDIR, "voxkey_stdout.log")
WATCHDOG_LOG = os.path.join(LOGDIR, "voxkey_watchdog.log")


def _wlog(msg: str):
    try:
        with open(WATCHDOG_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _find_real_app() -> list[str] | None:
    """Locate the real app executable / entry to launch."""
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    frozen = getattr(sys, "frozen", False)

    if frozen:
        # Built: the real app sits next to this watchdog.
        candidates = ("VoxKey-App.exe", "VoxKey-App",
                      os.path.join("VoxKey-App", "VoxKey-App.exe"))
        for name in candidates:
            cand = os.path.join(here, name)
            if os.path.exists(cand):
                return [cand]
        # fallback: search for it
        for root, _dirs, files in os.walk(here):
            for fn in files:
                if fn.lower() in ("voxkey-app.exe", "voxkey-app"):
                    return [os.path.join(root, fn)]
        return None
    else:
        # Source: run the module directly.
        return [sys.executable, "-m", "whispr"]


def _read_crash_details() -> str:
    """Assemble the most useful crash info from whatever logs exist."""
    parts = []
    parts.append(f"VoxKey crash — {datetime.now():%Y-%m-%d %H:%M:%S}\n")

    # native fault trace (from faulthandler) — the key one for hard crashes
    if os.path.exists(FAULT_LOG):
        try:
            txt = open(FAULT_LOG, encoding="utf-8", errors="replace").read().strip()
            if txt:
                parts.append("=== NATIVE CRASH TRACE (faulthandler) ===\n" + txt + "\n")
        except Exception:
            pass

    # python-level crash report (from crash_reporter, if it ran)
    if os.path.exists(CRASH_LOG):
        try:
            txt = open(CRASH_LOG, encoding="utf-8", errors="replace").read().strip()
            if txt:
                parts.append("=== APP LOG (last 2000 chars) ===\n" + txt[-2000:] + "\n")
        except Exception:
            pass

    # stdout/stderr redirect (may hold Qt messages)
    if os.path.exists(STDOUT_LOG):
        try:
            txt = open(STDOUT_LOG, encoding="utf-8", errors="replace").read().strip()
            if txt:
                parts.append("=== OUTPUT (last 1000 chars) ===\n" + txt[-1000:] + "\n")
        except Exception:
            pass

    if len(parts) == 1:
        parts.append("(No detailed trace was captured. The crash may have been "
                     "at a very low level. The exit code is shown above.)\n")
    return "\n".join(parts)


def _show_xp_dialog(exit_code: int, details: str):
    """
    Show an XP-style 'application has encountered a problem' dialog with a
    'Show log' button. Uses PyQt if available, else a native Win32 MessageBox.
    """
    summary = (
        "VoxKey has encountered a problem and needs to close.\n"
        "We are sorry for the inconvenience.\n\n"
        f"(exit code: {exit_code})"
    )
    _wlog(f"showing crash dialog, exit_code={exit_code}")

    # Try a proper dialog with a Show-log button (PyQt).
    try:
        from PyQt6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        box = QtWidgets.QMessageBox()
        box.setIcon(QtWidgets.QMessageBox.Icon.Critical)
        box.setWindowTitle("VoxKey")
        box.setText("VoxKey has encountered a problem and needs to close.")
        box.setInformativeText(
            "We're sorry for the inconvenience.\n\n"
            "An error report has been saved on your computer. "
            "Click 'Show log' to see what happened.")
        show_btn = box.addButton("Show log", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        folder_btn = box.addButton("Open log folder", QtWidgets.QMessageBox.ButtonRole.ActionRole)
        box.addButton("Close", QtWidgets.QMessageBox.ButtonRole.RejectRole)
        box.setDetailedText(details)  # expandable "Details" area holds the full log
        box.exec()

        clicked = box.clickedButton()
        if clicked == show_btn:
            _open_path(CRASH_LOG if os.path.exists(CRASH_LOG) else FAULT_LOG)
        elif clicked == folder_btn:
            _open_path(LOGDIR)
        return
    except Exception as e:
        _wlog(f"PyQt dialog failed: {e}")

    # Fallback: native Win32 MessageBox (always works, no dependencies).
    try:
        if os.name == "nt":
            import ctypes
            # MB_ICONERROR | MB_YESNO ; Yes = open log
            MB_ICONERROR = 0x10
            MB_YESNO = 0x04
            IDYES = 6
            res = ctypes.windll.user32.MessageBoxW(
                0,
                summary + "\n\nClick 'Yes' to open the error log.",
                "VoxKey - Application Error",
                MB_ICONERROR | MB_YESNO)
            if res == IDYES:
                _open_path(CRASH_LOG if os.path.exists(CRASH_LOG) else FAULT_LOG)
            return
    except Exception as e:
        _wlog(f"Win32 MessageBox failed: {e}")

    # Last resort: write everything to a visible file and open it.
    try:
        report = os.path.join(LOGDIR, "CRASH_REPORT.txt")
        with open(report, "w", encoding="utf-8") as f:
            f.write(summary + "\n\n" + details)
        _open_path(report)
    except Exception:
        pass


def _open_path(path: str):
    try:
        if os.name == "nt":
            os.startfile(path)  # type: ignore
        else:
            subprocess.Popen(["xdg-open", path])
    except Exception:
        pass


def main():
    cmd = _find_real_app()
    if not cmd:
        _wlog("could not find the real app executable")
        _show_xp_dialog(-1, "Could not locate the VoxKey application files.\n"
                            "The installation may be incomplete.")
        return 1

    _wlog(f"launching: {cmd}")
    try:
        # Run the real app and WAIT for it. Inherit console off.
        proc = subprocess.run(cmd)
        code = proc.returncode
    except Exception as e:
        _wlog(f"failed to launch app: {e}")
        _show_xp_dialog(-1, f"VoxKey could not start:\n{e}")
        return 1

    _wlog(f"app exited with code {code}")

    # 0 = clean exit. Anything else = crash (native crashes give big/negative codes;
    # 0xC0000005 access violation shows as 3221225477 / -1073741819).
    if code != 0:
        details = _read_crash_details()
        _show_xp_dialog(code, details)
        return code
    return 0


if __name__ == "__main__":
    sys.exit(main())
