r"""
crash_reporter.py — an in-app crash dialog, like the old Windows error report.

Baked into VoxKey (not a separate tool). If ANYTHING goes wrong — at startup,
when opening the window, or when clicking a button — a dialog pops up telling
you what happened, with the full technical details viewable and copyable, plus
a button to open the log file.

How it captures crashes (three layers, so nothing slips through):
  1. sys.excepthook            — any unhandled Python exception, app-wide.
  2. a Qt message handler      — Qt's own C++-level warnings/fatals.
  3. @catch decorator + guard  — wraps risky UI callbacks (button clicks etc.)
     so a click that fails shows the dialog instead of killing the app.

Everything is also appended to  %APPDATA%\VoxKey\voxkey_crash.log .
"""
from __future__ import annotations
import os
import sys
import platform
import traceback
from datetime import datetime


def log_dir() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "VoxKey")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        d = os.path.expanduser("~")
    return d


CRASH_LOG = os.path.join(log_dir(), "voxkey_crash.log")


def _write(text: str) -> None:
    try:
        with open(CRASH_LOG, "a", encoding="utf-8") as f:
            f.write(text)
    except Exception:
        pass


def _header() -> str:
    return (
        f"\n{'=' * 64}\n"
        f"VoxKey crash report — {datetime.now():%Y-%m-%d %H:%M:%S}\n"
        f"Python {sys.version.split()[0]} | {platform.platform()}\n"
        f"Frozen exe: {getattr(sys, 'frozen', False)}\n"
        f"{'=' * 64}\n"
    )


def record(context: str, exc: BaseException | None = None,
           extra: str = "") -> str:
    """Write a crash entry to the log and return the full report text."""
    parts = [_header(), f"WHERE: {context}\n"]
    if exc is not None:
        parts.append("WHAT:  " + "".join(
            traceback.format_exception(type(exc), exc, exc.__traceback__)))
    if extra:
        parts.append("INFO:  " + extra + "\n")
    report = "".join(parts)
    _write(report)
    return report


def show_dialog(title: str, summary: str, details: str) -> None:
    """Pop up the crash window. Falls back to a native message box, then console."""
    try:
        from PyQt6 import QtWidgets, QtGui, QtCore
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)

        dlg = QtWidgets.QDialog()
        dlg.setWindowTitle("VoxKey — Problem Report")
        dlg.resize(680, 460)
        dlg.setStyleSheet("""
            QDialog { background:#111114; }
            QLabel  { color:#f2f2f4; }
            QLabel#h { font-size:16px; font-weight:600; color:#ff6b6b; }
            QPlainTextEdit { background:#0b0b0d; color:#d6d6dc;
                border:1px solid #2a2a30; border-radius:8px;
                font-family:Consolas,monospace; font-size:12px; }
            QPushButton { background:#1c1c20; color:#f2f2f4; border:1px solid #2a2a30;
                padding:8px 16px; border-radius:8px; }
            QPushButton:hover { background:#26262c; }
            QPushButton:default { background:#e6e6ea; color:#000; font-weight:600; }
        """)
        lay = QtWidgets.QVBoxLayout(dlg)

        h = QtWidgets.QLabel("VoxKey ran into a problem")
        h.setObjectName("h")
        lay.addWidget(h)

        msg = QtWidgets.QLabel(summary)
        msg.setWordWrap(True)
        lay.addWidget(msg)

        lay.addWidget(QtWidgets.QLabel("Technical details:"))
        box = QtWidgets.QPlainTextEdit()
        box.setReadOnly(True)
        box.setPlainText(details)
        lay.addWidget(box, 1)

        path_lbl = QtWidgets.QLabel(f"Saved to: {CRASH_LOG}")
        path_lbl.setStyleSheet("color:#8a8a92; font-size:11px;")
        path_lbl.setWordWrap(True)
        lay.addWidget(path_lbl)

        row = QtWidgets.QHBoxLayout()
        b_copy = QtWidgets.QPushButton("Copy details")
        b_open = QtWidgets.QPushButton("Open log file")
        b_close = QtWidgets.QPushButton("Close")
        b_close.setDefault(True)
        row.addWidget(b_copy)
        row.addWidget(b_open)
        row.addStretch(1)
        row.addWidget(b_close)
        lay.addLayout(row)

        def do_copy():
            QtWidgets.QApplication.clipboard().setText(details)
            b_copy.setText("Copied!")

        def do_open():
            try:
                if os.name == "nt":
                    os.startfile(CRASH_LOG)  # type: ignore
                else:
                    import subprocess
                    subprocess.Popen(["xdg-open", CRASH_LOG])
            except Exception:
                pass

        b_copy.clicked.connect(do_copy)
        b_open.clicked.connect(do_open)
        b_close.clicked.connect(dlg.accept)

        dlg.exec()
        return
    except Exception:
        pass

    # Fallback 1: a plain native message box
    try:
        from PyQt6 import QtWidgets
        app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        QtWidgets.QMessageBox.critical(None, title, f"{summary}\n\n{details[:1500]}")
        return
    except Exception:
        pass

    # Fallback 2: Windows native MessageBox via ctypes (works with no Qt at all)
    try:
        if os.name == "nt":
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f"{summary}\n\nSee: {CRASH_LOG}", title, 0x10)
            return
    except Exception:
        pass

    # Fallback 3: console
    try:
        print(title, summary, details, sep="\n")
    except Exception:
        pass


def report_and_show(context: str, exc: BaseException | None = None,
                    extra: str = "", fatal: bool = False) -> None:
    """Record a crash and show the dialog. The one call sites use."""
    details = record(context, exc, extra)
    summary = (f"Something went wrong while: {context}.\n"
               "The details below have been saved to a log file. "
               "You can copy them or open the log to send for a fix.")
    if fatal:
        summary = ("VoxKey couldn't start properly.\n" + summary)
    show_dialog("VoxKey — Problem Report", summary, details)


# ---------------------------------------------------------------- installers
def install(app_context: str = "app") -> None:
    """Install the global hooks. Call once, as early as possible."""

    # 1) Unhandled Python exceptions anywhere
    def _excepthook(exc_type, exc_value, exc_tb):
        try:
            report_and_show("an unexpected error (unhandled exception)",
                            exc_value)
        except Exception:
            pass
    sys.excepthook = _excepthook

    # 2) Qt's own message handler (captures C++-side warnings/criticals/fatals)
    try:
        from PyQt6 import QtCore

        def _qt_handler(mode, ctx, message):
            try:
                lvl = str(mode)
                _write(f"[Qt] {lvl}: {message}\n")
                # Only pop the dialog for fatal/critical Qt messages
                if "Fatal" in lvl or "Critical" in lvl:
                    report_and_show("a Qt graphics error",
                                    None, extra=str(message))
            except Exception:
                pass
        QtCore.qInstallMessageHandler(_qt_handler)
    except Exception:
        pass


def catch(context: str):
    """
    Decorator for UI callbacks (button clicks, menu actions). If the wrapped
    function raises, show the crash dialog instead of letting it kill the app.
    """
    def deco(fn):
        def wrapper(*a, **k):
            try:
                return fn(*a, **k)
            except Exception as e:
                report_and_show(context, e)
                return None
        return wrapper
    return deco
