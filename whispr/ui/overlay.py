"""
overlay.py — the floating recording pill, styled to match Wispr.

Wispr's overlay is a small, dark, rounded "pill" that floats near the bottom of
the screen with a symmetric waveform that pulses from the centre outward. This
recreates that look:

  - compact rounded-rect pill, near-black with a subtle border
  - soft drop shadow for the "floating" feel
  - centre-anchored, MIRRORED waveform bars (top and bottom halves symmetric)
  - bars ease smoothly toward the live level (no jitter), with a gentle idle
    shimmer so it looks alive even in silence
  - a small state dot that pulses while listening

Appears ONLY while the hotkey is held. Frameless, always-on-top, click-through.
PyQt6, guarded so the module still imports on headless machines.
"""
from __future__ import annotations
import math
import random

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    _HAVE_QT = True
except Exception:  # pragma: no cover
    _HAVE_QT = False


if _HAVE_QT:
    class WaveformOverlay(QtWidgets.QWidget):
        def __init__(self, level_provider, config=None):
            super().__init__()
            self.level_provider = level_provider
            self.config = config
            self._drag_pos = None

            self.n_bars = 28
            self.display = [0.06] * self.n_bars
            self.phase = 0.0

            self.setWindowFlags(
                QtCore.Qt.WindowType.FramelessWindowHint
                | QtCore.Qt.WindowType.WindowStaysOnTopHint
                | QtCore.Qt.WindowType.Tool
                # Never take keyboard focus. If the overlay activates, the
                # simulated Ctrl+V lands on IT instead of the document you
                # were typing into — and nothing appears.
                | QtCore.Qt.WindowType.WindowDoesNotAcceptFocus
            )
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(QtCore.Qt.WidgetAttribute.WA_ShowWithoutActivating)
            # Belt and braces on Windows: WS_EX_NOACTIVATE so the OS itself
            # refuses to give this window focus.
            try:
                import os as _os
                if _os.name == "nt":
                    import ctypes as _ct
                    GWL_EXSTYLE, WS_EX_NOACTIVATE = -20, 0x08000000
                    hwnd = int(self.winId())
                    u32 = _ct.windll.user32
                    cur = u32.GetWindowLongW(hwnd, GWL_EXSTYLE)
                    u32.SetWindowLongW(hwnd, GWL_EXSTYLE, cur | WS_EX_NOACTIVATE)
            except Exception:
                pass

            self.margin = 20
            self.pill_w, self.pill_h = 168, 52
            self.resize(self.pill_w + self.margin * 2, self.pill_h + self.margin * 2)

            self.timer = QtCore.QTimer(self)
            self.timer.timeout.connect(self._tick)

        def _position(self):
            if (self.config is not None and self.config.overlay_x is not None
                    and self.config.overlay_y is not None):
                self.move(self.config.overlay_x, self.config.overlay_y)
                return
            screen = QtWidgets.QApplication.primaryScreen().availableGeometry()
            x = screen.center().x() - self.width() // 2
            y = screen.bottom() - self.height() - 40
            self.move(x, y)

        def mousePressEvent(self, e):
            if e.button() == QtCore.Qt.MouseButton.LeftButton:
                self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
                e.accept()

        def mouseMoveEvent(self, e):
            if self._drag_pos is not None:
                self.move(e.globalPosition().toPoint() - self._drag_pos)
                e.accept()

        def mouseReleaseEvent(self, e):
            self._drag_pos = None
            if self.config is not None:
                self.config.overlay_x = self.x()
                self.config.overlay_y = self.y()
                try:
                    self.config.save()
                except Exception:
                    pass

        def show_overlay(self):
            self._position()
            self.display = [0.06] * self.n_bars
            self.show()
            self.raise_()
            self.timer.start(16)
            # Failsafe: if the key-release event is ever missed (common with the
            # Windows key), auto-hide after 30s so the overlay can't get stuck.
            if not hasattr(self, "_failsafe"):
                self._failsafe = QtCore.QTimer(self)
                self._failsafe.setSingleShot(True)
                self._failsafe.timeout.connect(self.hide_overlay)
            self._failsafe.start(30000)

        def hide_overlay(self):
            self.timer.stop()
            if hasattr(self, "_failsafe"):
                self._failsafe.stop()
            self.hide()
            self.close()   # ensure the frameless tool window fully goes away

        def _tick(self):
            self.phase += 0.18
            try:
                level = float(self.level_provider())
            except Exception:
                level = 0.0
            level = max(0.0, min(1.0, level * 6.5))

            centre = self.n_bars // 2
            idle = 0.05 + 0.03 * (0.5 + 0.5 * math.sin(self.phase))
            target_centre = max(idle, level) * (0.85 + 0.3 * random.random())

            for i in range(self.n_bars):
                dist = abs(i - centre) / max(1, centre)
                falloff = math.cos(dist * math.pi / 2) ** 1.5
                wobble = 0.10 * math.sin(self.phase * 1.3 + i * 0.6)
                target = max(idle, target_centre * falloff + wobble * level)
                target = max(0.03, min(1.0, target))
                a = 0.55 if target > self.display[i] else 0.28
                self.display[i] += (target - self.display[i]) * a
            self.update()

        def paintEvent(self, _e):
            p = QtGui.QPainter(self)
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

            ox, oy = self.margin, self.margin
            rect = QtCore.QRectF(ox, oy, self.pill_w, self.pill_h)
            radius = self.pill_h / 2

            for i in range(6, 0, -1):
                alpha = 8 * i
                sh = QtGui.QColor(0, 0, 0, alpha)
                sp = QtGui.QPainterPath()
                sr = rect.adjusted(-i, -i + 3, i, i + 3)
                sp.addRoundedRect(sr, radius + i, radius + i)
                p.fillPath(sp, sh)

            path = QtGui.QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            p.fillPath(path, QtGui.QColor(18, 18, 20, 245))
            pen = QtGui.QPen(QtGui.QColor(255, 255, 255, 26))
            pen.setWidthF(1.0)
            p.setPen(pen)
            p.drawPath(path)

            pulse = 0.5 + 0.5 * math.sin(self.phase * 0.9)
            dot_c = QtGui.QColor(255, 92, 92, int(200 + 55 * pulse))
            p.setPen(QtCore.Qt.PenStyle.NoPen)
            p.setBrush(dot_c)
            dot_cx = ox + 20
            dot_cy = oy + self.pill_h / 2
            p.drawEllipse(QtCore.QPointF(dot_cx, dot_cy), 4.5, 4.5)

            left = ox + 38
            right = ox + self.pill_w - 16
            span = right - left
            gap = span / self.n_bars
            bar_w = gap * 0.5
            mid_y = oy + self.pill_h / 2
            max_h = self.pill_h * 0.34

            pen = QtGui.QPen(QtGui.QColor(238, 240, 245))
            pen.setWidthF(bar_w)
            pen.setCapStyle(QtCore.Qt.PenCapStyle.RoundCap)
            p.setPen(pen)
            for i, v in enumerate(self.display):
                x = left + i * gap + gap / 2
                h = max(1.5, v * max_h)
                p.drawLine(QtCore.QPointF(x, mid_y - h),
                           QtCore.QPointF(x, mid_y + h))
            p.end()

else:  # pragma: no cover
    class WaveformOverlay:
        def __init__(self, *a, **k): ...
        def show_overlay(self): ...
        def hide_overlay(self): ...
