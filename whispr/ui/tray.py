"""
tray.py — the background daemon: system tray icon + glue.

This is what runs continuously. It:
  - loads config, corrections, transcript store
  - builds the real recorder / transcriber
  - starts the global hotkey listener (hold = record)
  - shows/hides the waveform overlay on the record edges
  - runs transcription in a worker thread so the UI never blocks
  - exposes a tray menu (Open, Voice Setup, Settings, Quit)

The overlay + transcription are dispatched onto the Qt main thread via signals,
because pynput callbacks fire on their own thread.
"""
from __future__ import annotations
import sys
import threading
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets

from ..core.config import Config, config_dir
from ..core.corrections import CorrectionEngine
from ..core.transcripts import TranscriptStore
from ..core.recorder import Recorder, rms_level, apply_gain
from ..core.transcriber import Transcriber
from ..core.controller import DictationController, State
from ..core.hotkey import HotkeyListener
from ..core import output
from .overlay import WaveformOverlay
from .main_window import MainWindow


class _Signals(QtCore.QObject):
    show_overlay = QtCore.pyqtSignal()
    hide_overlay = QtCore.pyqtSignal()
    transcript_added = QtCore.pyqtSignal()


class AppContext:
    """Shared state handed to the UI widgets."""
    def __init__(self):
        self.dir = config_dir()
        self.config = Config.load()
        self.corrections = CorrectionEngine.load(self.dir / "corrections.json")
        self.store = TranscriptStore(self.dir / "transcripts.jsonl")

        # If the saved input device is no longer valid, reset to system default
        try:
            from ..core.recorder import resolve_device
            idx, why = resolve_device(
                getattr(self.config, "input_device_name", None),
                self.config.input_device_index)
            from ..core.applog import log
            log(f"startup device resolution: {why}")
        except Exception:
            pass
        # validate_device done
        self.recorder = Recorder(
            sample_rate=self.config.sample_rate,
            device_index=self.config.input_device_index,
            device_name=getattr(self.config, "input_device_name", None),
            gain=self.config.mic_gain,
        )
        self.transcriber = Transcriber(
            model_size=self.config.model_size,
            device=self.config.device,
            compute_type=self.config.compute_type,
            language=self.config.language,
            engine_python=getattr(self.config, "engine_python", None),
            cpu_threads=getattr(self.config, "cpu_threads", 0),
            beam_size=getattr(self.config, "beam_size", 1),
        )
        self.signals = _Signals()

        self.controller = DictationController(
            config=self.config,
            recorder=self.recorder,
            transcriber=self.transcriber,
            corrections=self.corrections,
            store=self.store,
            paste_fn=output.paste_text,
            window_title_fn=output.active_window_title,
            on_state_change=self._on_state,
            on_status=self._on_status,
        )
        self.transcriber.on_status = self._on_status
        self.window: MainWindow | None = None
        self.overlay = WaveformOverlay(level_provider=lambda: self.recorder.level, config=self.config)
        self.hotkey = HotkeyListener(
            self.config.hotkey,
            on_activate=self._hotkey_down,
            on_deactivate=self._hotkey_up,
        )

    # -------------------------------------------------- hotkey edges
    def _hotkey_down(self):
        from ..core.applog import log
        log("hotkey down — recording")
        self.signals.show_overlay.emit()
        try:
            self.controller.on_hotkey_down()
        except Exception as e:
            from ..core.applog import log_exception
            log_exception("starting the microphone", e)
            self._on_status(f"Microphone could not start: {e}")
            self.signals.hide_overlay.emit()

    def _hotkey_up(self):
        self.signals.hide_overlay.emit()
        # heavy work off the hotkey thread
        threading.Thread(target=self._finish, daemon=True).start()

    def _finish(self):
        from ..core import crash_reporter
        from ..core.applog import log
        log("hotkey up — transcribing")
        try:
            self.controller.on_hotkey_up()
            log("dictation finished")
            self.signals.transcript_added.emit()
        except Exception as e:
            crash_reporter.record("dictation processing", e)
            self._notify("VoxKey", f"Dictation failed: {e}")
        finally:
            # belt-and-braces: make sure the overlay is hidden no matter what
            self.signals.hide_overlay.emit()

    def _on_state(self, s: State):
        pass  # hook for future status UI

    # status messages ("no speech detected", "loading model...") are shown by
    # the tray; the callback is set from TrayApp once the icon exists.
    notify_fn = None

    def _on_status(self, msg: str):
        from ..core.applog import log
        log(f"status: {msg}")
        if callable(self.notify_fn):
            try:
                self.notify_fn("VoxKey", msg)
            except Exception:
                pass

    # -------------------------------------------------- settings/persistence
    def save_corrections(self):
        self.corrections.save(self.dir / "corrections.json")

    def pause_hotkey(self):
        """Temporarily stop the global listener (used while capturing a new key)."""
        try:
            self.hotkey.stop()
        except Exception:
            pass

    def resume_hotkey(self, combo: str = None):
        """Restart the global listener, optionally with a new combo."""
        try:
            if combo:
                self.config.hotkey = combo
                self.hotkey.set_combo(combo)
            self.hotkey.start()
        except Exception:
            pass

    def on_settings_changed(self):
        self.recorder.gain = self.config.mic_gain
        self.recorder.device_index = self.config.input_device_index
        self.recorder.device_name = getattr(
            self.config, "input_device_name", None)
        try:
            self.transcriber.shutdown()
        except Exception:
            pass
        self.transcriber = Transcriber(
            model_size=self.config.model_size, device=self.config.device,
            compute_type=self.config.compute_type,
            language=self.config.language,
            engine_python=getattr(self.config, "engine_python", None),
            cpu_threads=getattr(self.config, "cpu_threads", 0),
            beam_size=getattr(self.config, "beam_size", 1))
        self.transcriber.on_status = self._on_status
        self.controller.transcriber = self.transcriber
        self.hotkey.set_combo(self.config.hotkey)
        threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self):
        """Load the speech model in the background worker process so the first
        dictation doesn't sit there for 20 seconds. Safe: it runs in a separate
        process, so even a native crash can't take the app down."""
        from ..core.applog import log_exception
        try:
            self.transcriber.load()
        except Exception as e:
            log_exception("model warm-up", e)
            self._on_status(f"Speech engine failed to start: {e}")

    def calibrate_once(self, sentence=""):
        """Record until the user speaks (up to 6s). Saves the sample to the
        training profile. Returns (noise_rms, speech_rms)."""
        import time, numpy as np, os, json
        self.recorder.start()
        # capture up to 6s, but stop ~0.8s after speech ends
        sr = self.config.sample_rate
        frames = []
        t0 = time.time()
        spoke = False
        silence_after = 0.0
        while time.time() - t0 < 6.0:
            time.sleep(0.1)
            lvl = self.recorder.level
            if lvl > self.config.silence_threshold * 1.5:
                spoke = True
                silence_after = 0.0
            elif spoke:
                silence_after += 0.1
                if silence_after > 0.8:
                    break
        audio = self.recorder.stop()
        if len(audio) == 0:
            return 0.0, 0.0
        noise = audio[: int(sr * 0.4)]
        speech = audio[int(sr * 0.4):]
        # save the sample to a training profile
        try:
            prof = self.dir / "training_profile.json"
            data = {"samples": []}
            if prof.exists():
                data = json.loads(prof.read_text())
            data["samples"].append({
                "sentence": sentence,
                "noise_rms": round(float(rms_level(noise)), 5),
                "speech_rms": round(float(rms_level(speech)), 5),
                "duration_s": round(len(audio) / sr, 2),
            })
            prof.write_text(json.dumps(data, indent=2))
        except Exception:
            pass
        return rms_level(noise), rms_level(speech)


class TrayApp:
    def __init__(self):
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
        self.app.setQuitOnLastWindowClosed(False)  # keep running in tray
        self.app.setApplicationName("VoxKey")

        # Build shared state. If the mic/model setup throws, we still want the
        # app to run, so failures here are captured rather than fatal.
        self.ctx = AppContext()

        self.ctx.signals.show_overlay.connect(self.ctx.overlay.show_overlay)
        self.ctx.signals.hide_overlay.connect(self.ctx.overlay.hide_overlay)
        self.ctx.signals.transcript_added.connect(self._refresh_window)

        # System tray may not be available immediately after login/install.
        self.tray = None
        self.menu = None
        try:
            if QtWidgets.QSystemTrayIcon.isSystemTrayAvailable():
                # Parent the icon to the app and keep the menu as an attribute,
                # so neither gets garbage-collected (a classic PyQt tray crash).
                self.tray = QtWidgets.QSystemTrayIcon(self.app)
                self.tray.setIcon(self._make_icon())
                self.tray.setToolTip("VoxKey — hold your hotkey to dictate")
                self.menu = QtWidgets.QMenu()
                self.menu.addAction("Open VoxKey", self.open_window)
                self.menu.addAction("Voice Setup", lambda: self.open_window(tab=1))
                self.menu.addAction("Settings", lambda: self.open_window(tab=2))
                self.menu.addAction("Open log folder", self.open_log_folder)
                self.menu.addSeparator()
                self.menu.addAction("Quit", self.quit)
                self.tray.setContextMenu(self.menu)
                self.tray.activated.connect(self._on_tray_activated)
                self.tray.show()
        except Exception as e:
            from ..core.applog import log_exception
            log_exception("tray setup", e)
            self.tray = None

        # If there's no tray, open the main window so the app is usable and
        # visible (otherwise it would look like nothing happened).
        if self.tray is None:
            self.open_window()

        # Notifications from the controller/transcriber go to the tray balloon.
        self.ctx.notify_fn = self._notify

        # Pre-load the model in the WORKER PROCESS (not in this one), so the
        # first dictation is instant and any download happens up front.
        threading.Thread(target=self.ctx._warm, daemon=True).start()

        # NOTE: we deliberately do NOT load the speech model in-process here.
        # Pre-loading on a startup thread was crashing natively inside
        # faster-whisper/ctranslate2 during the first-run model download,
        # which took the whole app down. Instead the model loads lazily and
        # safely the first time you actually dictate (see _finish / transcribe),
        # so the app is stable and opens instantly.

        # Start the global hotkey listener. Never let this kill startup.
        try:
            self.ctx.hotkey.start()
        except Exception as e:
            self._notify("VoxKey", f"Hotkey listener could not start: {e}")

    def _on_tray_activated(self, reason):
        from ..core import crash_reporter
        try:
            if reason == QtWidgets.QSystemTrayIcon.ActivationReason.Trigger:
                self.open_window()
        except Exception as e:
            crash_reporter.report_and_show("clicking the tray icon", e)

    def _notify(self, title, msg):
        try:
            if self.tray is not None:
                self.tray.showMessage(title, msg)
            else:
                print(f"{title}: {msg}")
        except Exception:
            pass

    def _warm_model(self):
        try:
            self.ctx.transcriber.load()
            if self.tray is not None:
                self.tray.setToolTip("VoxKey — ready. Hold your hotkey to dictate.")
        except Exception as e:
            if self.tray is not None:
                self.tray.setToolTip(f"VoxKey — model load failed: {e}")

    def _make_icon(self) -> QtGui.QIcon:
        # Prefer the bundled brand icon if present (installer generates it)
        try:
            from ..core.assets import asset_path
            ico = asset_path("voxkey.ico")
            if ico:
                ic = QtGui.QIcon(ico)
                if not ic.isNull():
                    return ic
        except Exception as e:
            from ..core.applog import log_exception
            log_exception("load icon file", e)
        # Draw a simple fallback icon
        try:
            pm = QtGui.QPixmap(64, 64); pm.fill(QtCore.Qt.GlobalColor.transparent)
            p = QtGui.QPainter(pm)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            p.setBrush(QtGui.QColor(15, 15, 17)); p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.drawRoundedRect(6, 6, 52, 52, 16, 16)
            p.setPen(QtGui.QPen(QtGui.QColor(240, 240, 245), 4,
                                cap=QtCore.Qt.PenCapStyle.RoundCap))
            for i, h in enumerate([12, 24, 34, 20, 10]):
                x = 16 + i * 8
                p.drawLine(x, 32 - h // 2, x, 32 + h // 2)
            p.end()
            return QtGui.QIcon(pm)
        except Exception:
            # absolute last resort: a built-in standard icon (never fails)
            return self.app.style().standardIcon(
                QtWidgets.QStyle.StandardPixmap.SP_ComputerIcon)

    def open_window(self, tab: int = 0):
        from ..core import crash_reporter
        try:
            crash_reporter.record("open_window", None,
                                  extra=f"opening window on tab {tab}")
            if self.ctx.window is None:
                self.ctx.window = MainWindow(self.ctx)
            w = self.ctx.window
            w.refresh_transcripts()
            if hasattr(w, "_tabs"):
                w._tabs.setCurrentIndex(tab)
            w.show(); w.raise_(); w.activateWindow()
        except Exception as e:
            crash_reporter.report_and_show("opening the VoxKey window", e)

    def open_log_folder(self):
        """Show %APPDATA%\\VoxKey so the runtime log is one click away."""
        try:
            import os
            from ..core.applog import LOG_PATH
            folder = os.path.dirname(LOG_PATH)
            if os.name == "nt":
                os.startfile(folder)  # noqa: S606
            else:
                QtGui.QDesktopServices.openUrl(
                    QtCore.QUrl.fromLocalFile(folder))
        except Exception as e:
            self._notify("VoxKey", f"Could not open the log folder: {e}")

    def _refresh_window(self):
        if self.ctx.window and self.ctx.window.isVisible():
            self.ctx.window.refresh_transcripts()

    def quit(self):
        try:
            self.ctx.hotkey.stop()
        except Exception:
            pass
        try:
            self.ctx.transcriber.shutdown()
        except Exception:
            pass
        if self.tray is not None:
            self.tray.hide()
        self.app.quit()

    def run(self):
        return self.app.exec()
