"""
main_window.py — the app window.

Tabs:
  Transcripts : scrollable list of transcript cards (newest first). Each card
                shows text, timestamp, target app. Double-click or right-click
                to correct. Corrections feed the learning engine.
  Voice Setup : calibration + custom vocabulary (the "voice trainer").
  Settings    : hotkey, model, mic, autostart, paste method.

PyQt6. Guarded so the module imports even without Qt (for headless testing).
"""
from __future__ import annotations
import time

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    _HAVE_QT = True
except Exception:  # pragma: no cover
    _HAVE_QT = False


if _HAVE_QT:
    class TranscriptCard(QtWidgets.QFrame):
        def __init__(self, transcript, on_edit, on_delete, on_copy):
            super().__init__()
            self.t = transcript
            self.on_edit = on_edit
            self.setObjectName("card")
            self.setStyleSheet("""
                #card { background:#141416; border:1px solid #222226;
                        border-radius:14px; }
                #card:hover { border:1px solid #3a3a42; }
                #meta { color:#77777f; font-size:11px; }
                #body { color:#f2f2f4; font-size:14px; line-height:1.5; }
            """)
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(14, 10, 14, 12)

            ts = time.strftime("%d %b %Y · %H:%M", time.localtime(transcript.created_at))
            meta = f"{ts}"
            if transcript.target_app:
                meta += f"   →  {transcript.target_app}"
            if transcript.edited:
                meta += "   · edited"
            m = QtWidgets.QLabel(meta); m.setObjectName("meta")

            self.body = QtWidgets.QLabel(transcript.text)
            self.body.setObjectName("body")
            self.body.setWordWrap(True)
            self.body.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse)

            row = QtWidgets.QHBoxLayout()
            btn_edit = QtWidgets.QPushButton("Correct")
            btn_copy = QtWidgets.QPushButton("Copy")
            btn_del = QtWidgets.QPushButton("Delete")
            for b in (btn_edit, btn_copy, btn_del):
                b.setCursor(QtCore.Qt.CursorShape.PointingHandCursor)
            btn_edit.clicked.connect(lambda: self._edit())
            btn_copy.clicked.connect(lambda: on_copy(self.t))
            btn_del.clicked.connect(lambda: on_delete(self.t))
            row.addWidget(btn_edit); row.addWidget(btn_copy)
            row.addStretch(1); row.addWidget(btn_del)

            lay.addWidget(m); lay.addWidget(self.body); lay.addLayout(row)
            self._on_delete = on_delete

        def contextMenuEvent(self, e):
            menu = QtWidgets.QMenu(self)
            menu.addAction("Correct text…", self._edit)
            menu.addAction("Correct selected word…", self._correct_selection)
            menu.exec(e.globalPos())

        def _edit(self):
            new, ok = QtWidgets.QInputDialog.getMultiLineText(
                self, "Correct transcript", "Edit the text. Changes teach the app:",
                self.t.text)
            if ok and new != self.t.text:
                self.on_edit(self.t, new)
                self.body.setText(new)

        def _correct_selection(self):
            sel = self.body.selectedText().strip()
            if not sel:
                QtWidgets.QMessageBox.information(
                    self, "No selection", "Select the wrong word first, then right-click.")
                return
            new, ok = QtWidgets.QInputDialog.getText(
                self, "Correct word", f"Replace '{sel}' with:")
            if ok and new:
                updated = self.t.text.replace(sel, new)
                self.on_edit(self.t, updated)
                self.body.setText(updated)


    class MainWindow(QtWidgets.QMainWindow):
        def __init__(self, app_ctx):
            super().__init__()
            self.ctx = app_ctx      # provides controller, store, config, corrections
            self.setWindowTitle("VOX Voice Zone")
            self.resize(680, 740)
            self._apply_theme()
            self._set_window_icon()

            central = QtWidgets.QWidget()
            root = QtWidgets.QVBoxLayout(central)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)
            root.addWidget(self._build_header())

            tabs = QtWidgets.QTabWidget()
            tabs.addTab(self._build_transcripts_tab(), "Transcripts")
            tabs.addTab(self._build_voice_tab(), "Voice Setup")
            tabs.addTab(self._build_settings_tab(), "Settings")
            self._tabs = tabs
            tabwrap = QtWidgets.QWidget()
            tw = QtWidgets.QVBoxLayout(tabwrap)
            tw.setContentsMargins(16, 4, 16, 12)
            tw.addWidget(tabs)
            root.addWidget(tabwrap, 1)
            self.setCentralWidget(central)
            self.refresh_transcripts()

        def _asset(self, name):
            from ..core.assets import asset_path
            return asset_path(name)

        def _set_window_icon(self):
            try:
                ico = self._asset("voxkey.ico")
                if ico:
                    icon = QtGui.QIcon(ico)
                    if not icon.isNull():
                        self.setWindowIcon(icon)
            except Exception:
                pass  # icon is cosmetic; never let it crash the window

        def _build_header(self):
            bar = QtWidgets.QWidget()
            bar.setStyleSheet("background:#000000;")
            lay = QtWidgets.QHBoxLayout(bar)
            lay.setContentsMargins(20, 14, 20, 14)
            logo = self._asset("logo_white.png")
            lbl = QtWidgets.QLabel()
            if logo:
                pm = QtGui.QPixmap(logo).scaledToHeight(
                    38, QtCore.Qt.TransformationMode.SmoothTransformation)
                lbl.setPixmap(pm)
            else:
                lbl.setText("VOX")
                lbl.setStyleSheet("color:#fff;font-size:22px;font-weight:600;")
            lay.addWidget(lbl)
            lay.addStretch(1)
            hint = QtWidgets.QLabel("hold your hotkey to dictate")
            hint.setStyleSheet("color:#6a6a72;font-size:12px;")
            lay.addWidget(hint)
            return bar

        # -------------------------------------------------- theme
        def _apply_theme(self):
            from .themes import stylesheet
            scheme = getattr(self.ctx.config, "color_scheme", "default")
            self.setStyleSheet(stylesheet(scheme))

        # -------------------------------------------------- transcripts tab
        def _build_transcripts_tab(self):
            w = QtWidgets.QWidget()
            lay = QtWidgets.QVBoxLayout(w)
            top = QtWidgets.QHBoxLayout()
            self.search = QtWidgets.QLineEdit()
            self.search.setPlaceholderText("Search transcripts…")
            self.search.textChanged.connect(self.refresh_transcripts)
            top.addWidget(self.search)
            lay.addLayout(top)

            self.scroll = QtWidgets.QScrollArea()
            self.scroll.setWidgetResizable(True)
            self.scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
            self.list_host = QtWidgets.QWidget()
            self.list_lay = QtWidgets.QVBoxLayout(self.list_host)
            self.list_lay.setSpacing(10)
            self.list_lay.addStretch(1)
            self.scroll.setWidget(self.list_host)
            lay.addWidget(self.scroll)
            return w

        def refresh_transcripts(self):
            # clear
            while self.list_lay.count() > 1:
                item = self.list_lay.takeAt(0)
                if item.widget():
                    item.widget().deleteLater()
            q = self.search.text() if hasattr(self, "search") else ""
            items = self.ctx.store.search(q) if q else self.ctx.store.all()
            if not items:
                empty = QtWidgets.QLabel(
                    "No transcripts yet.\nHold your hotkey and speak to create one.")
                empty.setStyleSheet("color:#6a6a74; padding:40px;")
                empty.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
                self.list_lay.insertWidget(0, empty)
                return
            for t in items:
                card = TranscriptCard(
                    t, on_edit=self._edit_transcript,
                    on_delete=self._delete_transcript,
                    on_copy=self._copy_transcript)
                self.list_lay.insertWidget(self.list_lay.count() - 1, card)

        def _edit_transcript(self, t, new_text):
            self.ctx.controller.apply_user_edit(t.id, new_text)
            self.ctx.save_corrections()

        def _delete_transcript(self, t):
            self.ctx.store.delete(t.id)
            self.refresh_transcripts()

        def _copy_transcript(self, t):
            QtWidgets.QApplication.clipboard().setText(t.text)

        # -------------------------------------------------- voice setup tab
        def _build_voice_tab(self):
            from .voice_setup import build_voice_setup_widget
            return build_voice_setup_widget(self.ctx)

        # -------------------------------------------------- settings tab
        def _build_settings_tab(self):
            w = QtWidgets.QWidget()
            form = QtWidgets.QFormLayout(w)
            cfg = self.ctx.config

            from .hotkey_capture import HotkeyCaptureButton
            self.hotkey_capture = HotkeyCaptureButton(
                cfg.hotkey,
                pause_listener=self.ctx.pause_hotkey,
                resume_listener=self.ctx.resume_hotkey,
            )
            self.hotkey_capture.combo_changed.connect(self._on_hotkey_captured)

            self.model_box = QtWidgets.QComboBox()
            # Ordered fastest -> most accurate. The ".en" models are English
            # only: better AND faster than their multilingual namesakes.
            self.model_box.addItems([
                "tiny.en", "base.en", "distil-small.en", "small.en",
                "distil-medium.en", "medium.en", "distil-large-v3",
                "tiny", "base", "small", "medium", "large-v3",
            ])
            self.model_box.setCurrentText(cfg.model_size)

            self.paste_box = QtWidgets.QComboBox()
            self.paste_box.addItem("Paste automatically (Ctrl+V for you)", "paste")
            self.paste_box.addItem("Type the text out", "type")
            self.paste_box.addItem("Copy only — I'll press Ctrl+V", "copy")
            _pm = {"clipboard": "paste"}.get(cfg.paste_method, cfg.paste_method)
            for _i in range(self.paste_box.count()):
                if self.paste_box.itemData(_i) == _pm:
                    self.paste_box.setCurrentIndex(_i)
                    break

            self.autogain_box = QtWidgets.QCheckBox(
                "Amplify my voice automatically")
            self.autogain_box.setChecked(getattr(cfg, "auto_gain", True))
            self.autogain_box.setToolTip(
                "Brings quiet speech up to the level the model expects, and "
                "eases off when you're loud. Usually better than a fixed "
                "microphone gain.")

            self.autostart_box = QtWidgets.QCheckBox("Launch VoxKey at login")
            self.autostart_box.setChecked(cfg.autostart)

            self.uk_box = QtWidgets.QCheckBox("British English spelling")
            self.uk_box.setChecked(getattr(cfg, "british_english", True))

            from .themes import SCHEME_LABELS
            self.scheme_box = QtWidgets.QComboBox()
            for key, label in SCHEME_LABELS.items():
                self.scheme_box.addItem(label, key)
            cur_scheme = getattr(cfg, "color_scheme", "default")
            for i in range(self.scheme_box.count()):
                if self.scheme_box.itemData(i) == cur_scheme:
                    self.scheme_box.setCurrentIndex(i); break

            save = QtWidgets.QPushButton("Save settings")
            save.clicked.connect(self._save_settings)

            form.addRow("Push-to-talk hotkey", self.hotkey_capture)
            form.addRow("Whisper model", self.model_box)
            form.addRow("Colour scheme", self.scheme_box)
            form.addRow("When I finish speaking", self.paste_box)
            form.addRow("", self.autogain_box)
            form.addRow("", self.uk_box)
            form.addRow("", self.autostart_box)
            form.addRow("", save)
            hint = QtWidgets.QLabel(
                "Bigger models = more accurate, slower. 'small' is a good "
                "balance on CPU; 'large-v3-turbo' if you have a GPU.")
            hint.setStyleSheet("color:#8a8a94;")
            hint.setWordWrap(True)
            form.addRow("", hint)
            return w

        def _on_hotkey_captured(self, combo):
            # Persist immediately when a new hotkey is captured, and apply live.
            self.ctx.config.hotkey = combo
            self.ctx.config.save()
            self.ctx.on_settings_changed()

        def _save_settings(self):
            cfg = self.ctx.config
            cfg.hotkey = self.hotkey_capture.current_combo() or "<ctrl>+<shift>"
            cfg.model_size = self.model_box.currentText()
            cfg.auto_gain = self.autogain_box.isChecked()
            cfg.color_scheme = self.scheme_box.currentData()
            cfg.paste_method = self.paste_box.currentData()
            cfg.autostart = self.autostart_box.isChecked()
            cfg.british_english = self.uk_box.isChecked()
            cfg.save()
            try:
                from ..core.autostart import set_autostart
                set_autostart(cfg.autostart)
            except Exception:
                pass
            self.ctx.on_settings_changed()
            self._apply_theme()  # live-apply the colour scheme
            QtWidgets.QMessageBox.information(
                self, "Saved", "Settings saved. Colour scheme and hotkey applied.")

else:  # pragma: no cover
    class MainWindow:  # stub
        def __init__(self, *a, **k): ...
