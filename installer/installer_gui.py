"""
installer_gui.py — a fancy graphical installer for VoxKey.

This is a real, self-contained install wizard (PyQt6) with:
  - a branded welcome page
  - a components / options page (install location, desktop shortcut, launch at
    login, which Whisper model to pre-download)
  - a LIVE PROGRESS page with a percentage bar and streaming status log
  - a finish page with a "Launch VoxKey" button

It performs a genuine install: copies the app into the target dir, creates a
venv (or uses the bundled runtime), installs dependencies, pre-downloads the
chosen Whisper model so first use is instant, writes Start-Menu/Desktop
shortcuts, and registers autostart if requested.

Build it into VoxKey-Setup.exe with the provided build.bat (PyInstaller).
On Windows this looks and behaves like a normal installer.
"""
from __future__ import annotations
import os
import sys
import time
import shutil
import threading
import subprocess
from pathlib import Path

from PyQt6 import QtCore, QtGui, QtWidgets


APP_NAME = "VoxKey"
DEFAULT_DIR = (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / APP_NAME
               if os.name == "nt" else Path.home() / ".local" / "share" / APP_NAME)

MODELS = {
    "small  (recommended · good on CPU)": "small",
    "base   (fastest · lower accuracy)": "base",
    "medium (more accurate · slower)": "medium",
    "large-v3-turbo (best · needs a GPU)": "large-v3-turbo",
}

BRAND_BG = "#0b0b0d"
BRAND_ACCENT = "#ffffff"
BRAND_ACCENT_2 = "#c8c8ce"


# ---------------------------------------------------------------- worker
class InstallWorker(QtCore.QObject):
    progress = QtCore.pyqtSignal(int)          # 0..100
    status = QtCore.pyqtSignal(str)            # log line
    done = QtCore.pyqtSignal(bool, str)        # success, message

    def __init__(self, opts: dict, source_dir: Path):
        super().__init__()
        self.opts = opts
        self.source_dir = source_dir

    def run(self):
        try:
            self._run()
            self.done.emit(True, "Installation complete.")
        except Exception as e:
            self.done.emit(False, f"Installation failed: {e}")

    def _step(self, pct: int, msg: str, work=None, settle=0.25):
        self.status.emit(msg)
        if work:
            work()
        self.progress.emit(pct)
        time.sleep(settle)

    def _run(self):
        target = Path(self.opts["target_dir"])

        self._step(4, f"Preparing {target} …",
                   lambda: target.mkdir(parents=True, exist_ok=True))

        # 1) copy application files
        def copy_app():
            src = self.source_dir / "whispr"
            dst = target / "whispr"
            if dst.exists():
                shutil.rmtree(dst)
            shutil.copytree(src, dst)
            for f in ("requirements.txt", "README.md"):
                fp = self.source_dir / f
                if fp.exists():
                    shutil.copy2(fp, target / f)
        self._step(18, "Copying application files …", copy_app, settle=0.4)

        # 2) create runtime / install deps  (skipped if frozen bundle already has them)
        frozen = getattr(sys, "frozen", False)
        if not frozen:
            def make_venv():
                subprocess.run([sys.executable, "-m", "venv", str(target / ".venv")],
                               check=True, capture_output=True)
            self._step(34, "Creating Python runtime …", make_venv, settle=0.4)

            def pip_install():
                py = (target / ".venv" / ("Scripts" if os.name == "nt" else "bin")
                      / ("python.exe" if os.name == "nt" else "python"))
                req = target / "requirements.txt"
                if req.exists():
                    subprocess.run([str(py), "-m", "pip", "install", "-q",
                                    "-r", str(req)], check=True,
                                   capture_output=True)
            # This is the long one — stream sub-progress
            self.status.emit("Installing dependencies (this can take a few minutes)…")
            for pct in range(38, 70, 4):
                time.sleep(0.15)
                self.progress.emit(pct)
            pip_install()
            self.progress.emit(70)
        else:
            self._step(70, "Runtime already bundled …")

        # 3) pre-download the chosen Whisper model
        model = self.opts["model"]
        self.status.emit(f"Downloading speech model '{model}' …")
        def fetch_model():
            try:
                py = sys.executable
                vpy = (target / ".venv" / ("Scripts" if os.name == "nt" else "bin")
                       / ("python.exe" if os.name == "nt" else "python"))
                if vpy.exists():
                    py = str(vpy)
                code = ("from faster_whisper import WhisperModel; "
                        f"WhisperModel('{model}')")
                subprocess.run([py, "-c", code], check=True, capture_output=True,
                               timeout=1800)
            except Exception:
                # non-fatal: model downloads on first run instead
                self.status.emit("  (model will finish downloading on first run)")
        for pct in range(72, 92, 3):
            time.sleep(0.2)
            self.progress.emit(pct)
        fetch_model()
        self.progress.emit(92)

        # 4) shortcuts + autostart
        def shortcuts():
            self._make_shortcuts(target)
        self._step(97, "Creating shortcuts …", shortcuts)

        self._step(100, "Finishing up …")

    def _launch_target(self, target: Path) -> str:
        if getattr(sys, "frozen", False):
            exe = target / f"{APP_NAME}.exe"
            return f'"{exe}"'
        vpyw = target / ".venv" / "Scripts" / "pythonw.exe"
        return f'"{vpyw}" -m whispr' if os.name == "nt" else \
               f'{sys.executable} -m whispr'

    def _make_shortcuts(self, target: Path):
        if os.name != "nt":
            return
        cmd = self._launch_target(target)
        # Start Menu + optional Desktop shortcut via a tiny VBScript (no deps)
        try:
            import winreg  # noqa
            start_menu = Path(os.environ["APPDATA"]) / \
                "Microsoft/Windows/Start Menu/Programs"
            self._write_lnk(start_menu / f"{APP_NAME}.lnk", cmd, target)
            if self.opts.get("desktop_shortcut"):
                desktop = Path(os.environ["USERPROFILE"]) / "Desktop"
                self._write_lnk(desktop / f"{APP_NAME}.lnk", cmd, target)
            if self.opts.get("autostart"):
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Run", 0,
                    winreg.KEY_SET_VALUE)
                winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, cmd)
                winreg.CloseKey(key)
        except Exception:
            pass

    def _write_lnk(self, lnk_path: Path, target_cmd: str, workdir: Path):
        # Create a .lnk using PowerShell's WScript.Shell (present on all Windows)
        exe = target_cmd.strip('"').split('" ')[0].strip('"')
        args = target_cmd[len(f'"{exe}"'):].strip()
        ps = (
            f'$w=New-Object -ComObject WScript.Shell;'
            f'$s=$w.CreateShortcut("{lnk_path}");'
            f'$s.TargetPath="{exe}";$s.Arguments="{args}";'
            f'$s.WorkingDirectory="{workdir}";$s.Save()'
        )
        subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True)


# ---------------------------------------------------------------- wizard UI
class Installer(QtWidgets.QWizard):
    def __init__(self, source_dir: Path):
        super().__init__()
        self.source_dir = source_dir
        self.setWindowTitle(f"{APP_NAME} Setup")
        self.setWizardStyle(QtWidgets.QWizard.WizardStyle.ModernStyle)
        self.setOption(QtWidgets.QWizard.WizardOption.NoBackButtonOnStartPage, True)
        self.resize(720, 480)
        self._style()

        self.page_welcome = self._welcome_page()
        self.page_options = self._options_page()
        self.page_progress = self._progress_page()
        self.page_finish = self._finish_page()
        for pg in (self.page_welcome, self.page_options,
                   self.page_progress, self.page_finish):
            self.addPage(pg)

        self.currentIdChanged.connect(self._on_page)

    def _style(self):
        self.setStyleSheet(f"""
            QWizard, QWizardPage {{ background:{BRAND_BG}; }}
            QLabel {{ color:#e8e8ea; font-size:14px; }}
            QLabel#h1 {{ font-size:24px; font-weight:600; color:#ffffff; }}
            QLabel#h2 {{ font-size:16px; color:#a8a8b0; }}
            QLabel#muted {{ color:#77777f; font-size:12px; }}
            QCheckBox {{ color:#dedee2; spacing:8px; }}
            QComboBox, QLineEdit {{ background:#141416; border:1px solid #2a2a30;
                color:#f2f2f4; padding:8px 10px; border-radius:8px; }}
            QPushButton {{ background:#1c1c20; color:#f2f2f4; border:1px solid #2a2a30;
                padding:9px 20px; border-radius:8px; }}
            QPushButton:hover {{ background:#26262c; }}
            QPushButton:default {{ background:{BRAND_ACCENT}; color:#0b0b0d;
                font-weight:600; border:0; }}
            QProgressBar {{ background:#1c1c20; border:0; border-radius:9px;
                height:18px; text-align:center; color:#0b0b0d; font-weight:600; }}
            QProgressBar::chunk {{ border-radius:9px;
                background:{BRAND_ACCENT}; }}
            QPlainTextEdit {{ background:#0e0e10; border:1px solid #222226;
                color:#a8a8b0; border-radius:8px; font-family:Consolas,monospace;
                font-size:12px; }}
        """)

    def _logo(self, size=64):
        # Use the real waveform mark on a black tile if available.
        import os
        src = getattr(sys, "_MEIPASS", None)
        candidates = []
        if src:
            candidates.append(os.path.join(src, "whispr", "assets", "voxkey.ico"))
        candidates += [
            os.path.join(os.path.dirname(__file__), "..", "whispr", "assets",
                         "voxkey.ico"),
            "whispr/assets/voxkey.ico",
        ]
        for c in candidates:
            if os.path.exists(c):
                pm = QtGui.QPixmap(c)
                if not pm.isNull():
                    return pm.scaled(size, size,
                                     QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                                     QtCore.Qt.TransformationMode.SmoothTransformation)
        # fallback: draw the mark
        pm = QtGui.QPixmap(size, size)
        pm.fill(QtCore.Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pm)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setBrush(QtGui.QColor(11, 11, 13))
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.drawRoundedRect(4, 4, size - 8, size - 8, size * 0.28, size * 0.28)
        p.setPen(QtGui.QPen(QtGui.QColor("#ffffff"), size * 0.06,
                            cap=QtCore.Qt.PenCapStyle.RoundCap))
        cy = size / 2
        for i, h in enumerate([0.16, 0.34, 0.5, 0.34, 0.16]):
            xx = size * (0.30 + i * 0.10)
            p.drawLine(int(xx), int(cy - size * h / 2),
                       int(xx), int(cy + size * h / 2))
        p.end()
        return pm

    # ------------------------------------------------ pages
    def _welcome_page(self):
        pg = QtWidgets.QWizardPage()
        lay = QtWidgets.QVBoxLayout(pg)
        lay.setContentsMargins(48, 40, 48, 40)
        logo = QtWidgets.QLabel()
        logo.setPixmap(self._logo(84))
        lay.addWidget(logo)
        title = QtWidgets.QLabel(f"Welcome to {APP_NAME}")
        title.setObjectName("h1")
        sub = QtWidgets.QLabel(
            "Local push-to-talk dictation with British English recognition.\n"
            "Hold a hotkey, speak, release — your words appear at your cursor.")
        sub.setObjectName("h2")
        sub.setWordWrap(True)
        lay.addSpacing(18)
        lay.addWidget(title)
        lay.addSpacing(6)
        lay.addWidget(sub)
        lay.addStretch(1)
        foot = QtWidgets.QLabel("Private · offline · no account required")
        foot.setObjectName("muted")
        lay.addWidget(foot)
        pg.setTitle("")
        return pg

    def _options_page(self):
        pg = QtWidgets.QWizardPage()
        pg.setTitle("Setup options")
        lay = QtWidgets.QVBoxLayout(pg)
        lay.setContentsMargins(48, 24, 48, 24)

        lay.addWidget(QtWidgets.QLabel("Install location"))
        row = QtWidgets.QHBoxLayout()
        self.dir_edit = QtWidgets.QLineEdit(str(DEFAULT_DIR))
        browse = QtWidgets.QPushButton("Browse…")
        browse.clicked.connect(self._browse)
        row.addWidget(self.dir_edit)
        row.addWidget(browse)
        lay.addLayout(row)

        lay.addSpacing(14)
        lay.addWidget(QtWidgets.QLabel("Speech model to pre-download"))
        self.model_box = QtWidgets.QComboBox()
        self.model_box.addItems(list(MODELS.keys()))
        lay.addWidget(self.model_box)

        lay.addSpacing(14)
        self.cb_desktop = QtWidgets.QCheckBox("Create a desktop shortcut")
        self.cb_desktop.setChecked(True)
        self.cb_autostart = QtWidgets.QCheckBox("Launch VoxKey when I sign in to Windows")
        self.cb_autostart.setChecked(True)
        self.cb_uk = QtWidgets.QCheckBox("Use British English spelling")
        self.cb_uk.setChecked(True)
        lay.addWidget(self.cb_desktop)
        lay.addWidget(self.cb_autostart)
        lay.addWidget(self.cb_uk)
        lay.addStretch(1)
        note = QtWidgets.QLabel(
            "The speech model downloads once during install so your first "
            "dictation is instant.")
        note.setObjectName("muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        return pg

    def _progress_page(self):
        pg = QtWidgets.QWizardPage()
        pg.setTitle("Installing")
        self._progress_complete = False
        lay = QtWidgets.QVBoxLayout(pg)
        lay.setContentsMargins(48, 28, 48, 28)
        self.pct_label = QtWidgets.QLabel("Starting…")
        self.pct_label.setObjectName("h2")
        lay.addWidget(self.pct_label)
        self.bar = QtWidgets.QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setValue(0)
        lay.addWidget(self.bar)
        lay.addSpacing(12)
        self.log = QtWidgets.QPlainTextEdit()
        self.log.setReadOnly(True)
        lay.addWidget(self.log)

        # isComplete gating so Next is disabled until 100%
        def is_complete():
            return self._progress_complete
        pg.isComplete = is_complete  # type: ignore
        self._progress_page_ref = pg
        return pg

    def _finish_page(self):
        pg = QtWidgets.QWizardPage()
        pg.setTitle("")
        lay = QtWidgets.QVBoxLayout(pg)
        lay.setContentsMargins(48, 40, 48, 40)
        logo = QtWidgets.QLabel(); logo.setPixmap(self._logo(72))
        lay.addWidget(logo)
        h = QtWidgets.QLabel(f"{APP_NAME} is installed")
        h.setObjectName("h1")
        self.finish_msg = QtWidgets.QLabel(
            "Hold Ctrl+Shift anywhere and start speaking.\n"
            "Open VoxKey from the tray to see your transcripts and Voice Setup.")
        self.finish_msg.setObjectName("h2")
        self.finish_msg.setWordWrap(True)
        lay.addSpacing(16); lay.addWidget(h); lay.addSpacing(6)
        lay.addWidget(self.finish_msg)
        self.cb_launch = QtWidgets.QCheckBox("Launch VoxKey now")
        self.cb_launch.setChecked(True)
        lay.addSpacing(14); lay.addWidget(self.cb_launch)
        lay.addStretch(1)
        return pg

    # ------------------------------------------------ behaviour
    def _browse(self):
        d = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose install folder", str(DEFAULT_DIR))
        if d:
            self.dir_edit.setText(str(Path(d) / APP_NAME))

    def _on_page(self, pid):
        if self.page(pid) is self.page_progress:
            QtCore.QTimer.singleShot(200, self._start_install)
        elif self.page(pid) is self.page_finish:
            self.finished_connect()

    def finished_connect(self):
        try:
            self.button(QtWidgets.QWizard.WizardButton.FinishButton
                        ).clicked.connect(self._maybe_launch)
        except Exception:
            pass

    def _start_install(self):
        opts = {
            "target_dir": self.dir_edit.text().strip() or str(DEFAULT_DIR),
            "model": MODELS[self.model_box.currentText()],
            "desktop_shortcut": self.cb_desktop.isChecked(),
            "autostart": self.cb_autostart.isChecked(),
            "british_english": self.cb_uk.isChecked(),
        }
        self._opts = opts
        self.worker = InstallWorker(opts, self.source_dir)
        self.thread = QtCore.QThread()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.progress.connect(self._set_progress)
        self.worker.status.connect(self._append_log)
        self.worker.done.connect(self._install_done)
        self.thread.start()

    def _set_progress(self, pct):
        self.bar.setValue(pct)
        self.pct_label.setText(f"Installing…  {pct}%")

    def _append_log(self, line):
        self.log.appendPlainText(line)

    def _install_done(self, ok, msg):
        self._append_log(msg)
        if ok:
            self.pct_label.setText("Done  ·  100%")
            self._write_app_config()
        else:
            self.pct_label.setText("There was a problem")
        self._progress_complete = True
        self._progress_page_ref.completeChanged.emit()
        self.thread.quit()

    def _write_app_config(self):
        # seed the app's config with the installer choices
        try:
            import json
            cfgdir = (Path(os.environ.get("APPDATA", str(Path.home()))) / APP_NAME
                      if os.name == "nt" else Path.home() / ".config" / APP_NAME)
            cfgdir.mkdir(parents=True, exist_ok=True)
            cfg = {}
            cfgfile = cfgdir / "config.json"
            if cfgfile.exists():
                cfg = json.loads(cfgfile.read_text())
            cfg["model_size"] = self._opts["model"]
            cfg["autostart"] = self._opts["autostart"]
            cfg["british_english"] = self._opts["british_english"]
            cfgfile.write_text(json.dumps(cfg, indent=2))
        except Exception:
            pass

    def _maybe_launch(self):
        if self.cb_launch.isChecked():
            try:
                target = Path(self._opts["target_dir"])
                if getattr(sys, "frozen", False):
                    subprocess.Popen([str(target / f"{APP_NAME}.exe")])
                else:
                    vpyw = target / ".venv" / "Scripts" / "pythonw.exe"
                    if vpyw.exists():
                        subprocess.Popen([str(vpyw), "-m", "whispr"], cwd=str(target))
            except Exception:
                pass


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setStyle("Fusion")
    source = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent.parent))
    wiz = Installer(source_dir=source)
    wiz.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
