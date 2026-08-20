r"""
applog.py — lightweight crash logging for the real app.

Writes to %APPDATA%\VoxKey\voxkey_runtime.log so that when the packaged
VoxKey.exe misbehaves, there's a full traceback to read — the same visibility
always-on in the real app.

Also installs a global Qt/exception hook so *any* unhandled error is captured
instead of silently killing the app.
"""
from __future__ import annotations
import os
import sys
import traceback
from datetime import datetime


def _log_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "VoxKey")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~")
    return os.path.join(d, "voxkey_runtime.log")


LOG_PATH = _log_path()


def log(msg: str, level: str = "INFO") -> None:
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {level:5} {msg}"
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    # Only print if there's a real console (windowed exe has stdout=None,
    # and printing to it can crash the app on Windows).
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except Exception:
            pass


def log_exception(context: str, exc: BaseException) -> None:
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    log(f"EXCEPTION in {context}: {type(exc).__name__}: {exc}", "ERROR")
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(tb + "\n")
    except Exception:
        pass
    if sys.stdout is not None:
        try:
            print(tb, flush=True)
        except Exception:
            pass


def install_global_hook() -> None:
    """Route any unhandled Python exception to the log (log-only; showing a
    dialog from inside an excepthook can itself be unstable)."""
    def _hook(exc_type, exc_value, exc_tb):
        try:
            log_exception("unhandled", exc_value)
        except Exception:
            pass
    sys.excepthook = _hook


def guard(context: str):
    """Decorator: run a function, log any exception, and don't let it crash."""
    def deco(fn):
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except Exception as e:
                log_exception(context, e)
                try:
                    from PyQt6 import QtWidgets
                    QtWidgets.QMessageBox.critical(
                        None, "VoxKey error",
                        f"Something went wrong opening this part of VoxKey:\n\n"
                        f"{e}\n\nDetails saved to:\n{LOG_PATH}")
                except Exception:
                    pass
                return None
        return wrapper
    return deco
