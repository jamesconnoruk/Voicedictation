"""
Entry point:  python -m whispr   (or the built VoxKey.exe)

A crash reporter is installed FIRST, before anything else, so that any failure
— at startup, opening the window, or clicking a button — pops up a dialog
telling you what happened, with the details saved to a log you can open.
"""
import sys
import os

# Set Hugging Face options BEFORE anything imports faster-whisper, so the
# first-run model download can't crash on Windows symlink/cache issues.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("OMP_NUM_THREADS", "4")
import faulthandler


def _native_crash_log():
    """
    faulthandler catches LOW-LEVEL crashes (segfaults / access violations /
    0xC0000005) that Python try/except cannot — it writes a C-level traceback
    to a file at the moment the process dies. This is the only way to get info
    from a native Qt crash. The file is read by the watchdog to show the dialog.
    """
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "VoxKey")
    try:
        os.makedirs(d, exist_ok=True)
        fault_path = os.path.join(d, "voxkey_fault.log")
        # truncate previous, then arm faulthandler to write here on any fatal signal
        f = open(fault_path, "w", encoding="utf-8")
        faulthandler.enable(file=f, all_threads=True)
        return f
    except Exception:
        try:
            faulthandler.enable()
        except Exception:
            pass
        return None


_FAULT_FILE = _native_crash_log()


def _ensure_streams():
    """Windowed exe has stdout/stderr = None; any print() then crashes. Redirect."""
    if sys.stdout is None or sys.stderr is None:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "VoxKey")
        try:
            os.makedirs(d, exist_ok=True)
            f = open(os.path.join(d, "voxkey_stdout.log"), "a",
                     encoding="utf-8", buffering=1)
        except Exception:
            import io
            f = io.StringIO()
        if sys.stdout is None:
            sys.stdout = f
        if sys.stderr is None:
            sys.stderr = f


def main():
    _ensure_streams()

    # Install the crash reporter as early as possible.
    try:
        from whispr.core import crash_reporter
        crash_reporter.install()
        crash_reporter.record("startup", None, extra="VoxKey launching")
    except Exception:
        crash_reporter = None

    try:
        from whispr.ui.tray import TrayApp
        app = TrayApp()
        sys.exit(app.run())
    except SystemExit:
        raise
    except BaseException as e:
        # Startup failed — show the crash dialog with full details.
        if crash_reporter is not None:
            crash_reporter.report_and_show(
                "starting the application", e, fatal=True)
        else:
            # absolute fallback if even the reporter failed to import
            import traceback
            traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
