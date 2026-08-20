"""
hotkey_capture.py — a "press your hotkey" button for Settings.

Click it → it starts listening → physically press the key combination you want
(e.g. hold Ctrl+Shift) → release → that combo is captured and becomes the app's
dictation hotkey. Shows the combo live while you hold it, and a friendly label
when set.

Implementation notes:
  - While capturing, we temporarily PAUSE the global push-to-talk listener so it
    doesn't fire mid-capture, then resume with the new combo.
  - We grab the keyboard via a short-lived pynput listener and feed events into
    the pure HotkeyRecorder (already unit-tested). Qt key events would miss some
    modifiers, so pynput is the reliable source.
  - Guarded so the module imports without Qt for headless testing.
"""
from __future__ import annotations

from ..core.hotkey import HotkeyRecorder, pretty_combo

try:
    from PyQt6 import QtCore, QtWidgets
    _HAVE_QT = True
except Exception:  # pragma: no cover
    _HAVE_QT = False


if _HAVE_QT:
    class HotkeyCaptureButton(QtWidgets.QWidget):
        """Emits combo_changed(str) with a canonical combo like '<ctrl>+<shift>'."""
        combo_changed = QtCore.pyqtSignal(str)

        def __init__(self, initial_combo: str, pause_listener=None,
                     resume_listener=None):
            super().__init__()
            self._combo = initial_combo
            self._pause = pause_listener
            self._resume = resume_listener
            self._recorder: HotkeyRecorder | None = None
            self._listener = None
            self._poll = QtCore.QTimer(self)
            self._poll.timeout.connect(self._on_poll)

            lay = QtWidgets.QHBoxLayout(self)
            lay.setContentsMargins(0, 0, 0, 0)
            lay.setSpacing(10)

            self.display = QtWidgets.QLabel(pretty_combo(initial_combo) or "Not set")
            self.display.setStyleSheet(
                "background:#161925;border:1px solid #262a3a;border-radius:10px;"
                "padding:8px 14px;color:#eef1fb;font-weight:500;min-width:150px;")
            self.display.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)

            self.btn = QtWidgets.QPushButton("Set hotkey")
            self.btn.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            self.btn.clicked.connect(self._toggle_capture)

            lay.addWidget(self.display, 1)
            lay.addWidget(self.btn)

        # ---------------------------------------------------- capture control
        def _toggle_capture(self):
            if self._recorder is None:
                self._start_capture()
            else:
                self._cancel_capture()

        def _start_capture(self):
            if self._pause:
                try:
                    self._pause()
                except Exception:
                    pass
            self._recorder = HotkeyRecorder()
            self.btn.setText("Cancel")
            self.display.setText("Press keys…")
            self.display.setStyleSheet(
                "background:#1a2030;border:1px solid #6c8cff;border-radius:10px;"
                "padding:8px 14px;color:#9fb0ff;font-weight:500;min-width:150px;")
            self._start_listener()
            self._poll.start(40)

        def _finish_capture(self, combo: str):
            self._stop_listener()
            self._poll.stop()
            self._recorder = None
            self._combo = combo
            self.btn.setText("Set hotkey")
            self.display.setText(pretty_combo(combo))
            self.display.setStyleSheet(
                "background:#161925;border:1px solid #262a3a;border-radius:10px;"
                "padding:8px 14px;color:#eef1fb;font-weight:500;min-width:150px;")
            self.combo_changed.emit(combo)
            if self._resume:
                try:
                    self._resume(combo)
                except Exception:
                    pass

        def _cancel_capture(self):
            self._stop_listener()
            self._poll.stop()
            self._recorder = None
            self.btn.setText("Set hotkey")
            self.display.setText(pretty_combo(self._combo) or "Not set")
            self.display.setStyleSheet(
                "background:#161925;border:1px solid #262a3a;border-radius:10px;"
                "padding:8px 14px;color:#eef1fb;font-weight:500;min-width:150px;")
            if self._resume:
                try:
                    self._resume(self._combo)
                except Exception:
                    pass

        # ---------------------------------------------------- pynput plumbing
        def _key_name(self, key):
            from pynput import keyboard
            if isinstance(key, keyboard.Key):
                return key.name
            if isinstance(key, keyboard.KeyCode) and key.char:
                return key.char
            return str(key)

        def _start_listener(self):
            try:
                from pynput import keyboard
            except Exception:
                return
            self._listener = keyboard.Listener(
                on_press=lambda k: self._recorder and self._recorder.key_down(
                    self._key_name(k)),
                on_release=lambda k: self._recorder and self._recorder.key_up(
                    self._key_name(k)),
            )
            self._listener.start()

        def _stop_listener(self):
            if self._listener:
                self._listener.stop()
                self._listener = None

        def _on_poll(self):
            """Poll the recorder from the Qt thread for live display + lock-in."""
            if self._recorder is None:
                return
            if self._recorder.done and self._recorder.result:
                self._finish_capture(self._recorder.result)
                return
            snap = self._recorder.snapshot()
            if snap:
                self.display.setText(pretty_combo(snap))

        def current_combo(self) -> str:
            return self._combo

else:  # pragma: no cover
    class HotkeyCaptureButton:
        def __init__(self, *a, **k): ...
