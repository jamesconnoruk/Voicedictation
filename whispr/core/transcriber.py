"""
transcriber.py — local speech-to-text via faster-whisper, running in a
PERSISTENT background process (see transcribe_worker.py).

Key properties:
  * NO console window ever appears (CREATE_NO_WINDOW + a windowed worker exe).
  * The model is loaded ONCE, at startup, not on every dictation.
  * If the worker dies (including a hard native crash) we log it, restart it
    and retry once — the main app never goes down with it.
  * Worker stderr is drained into %APPDATA%\\VoxKey\\voxkey_runtime.log so a
    failure tells you WHY instead of "worker failed".
"""
from __future__ import annotations
import os
import sys
import json
import queue
import threading
import subprocess
import tempfile
import shutil

import numpy as np

from .applog import log, log_exception

# Windows: never flash a console window for the worker.
_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

WORKER_EXE_NAMES = ("VoxKey-Whisper.exe", "VoxKey-Whisper")


def _worker_search_dirs() -> list[str]:
    """Places the frozen worker exe might live, most likely first."""
    dirs = []
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        dirs += [
            # New layout: the worker has its OWN bundle in a subfolder, so its
            # DLLs can't be clobbered by Qt's copies of the same filenames.
            os.path.join(exe_dir, "whisper"),
            os.path.join(os.path.dirname(exe_dir), "whisper"),
            # Old flat layout, still supported.
            exe_dir,
            os.path.join(exe_dir, "_internal"),
            os.path.dirname(exe_dir),
        ]
    except Exception:
        pass
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            dirs += [meipass, os.path.dirname(meipass)]
    except Exception:
        pass
    seen, out = set(), []
    for d in dirs:
        if d and d not in seen:
            seen.add(d)
            out.append(d)
    return out


def find_worker_exe() -> str | None:
    for d in _worker_search_dirs():
        for name in WORKER_EXE_NAMES:
            cand = os.path.join(d, name)
            if os.path.exists(cand):
                return cand
    return None


def _worker_script_path() -> str | None:
    """Locate transcribe_worker.py — bundled as data in the frozen build."""
    here = os.path.dirname(os.path.abspath(__file__))
    cands = [os.path.join(here, "transcribe_worker.py")]
    try:
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            cands.append(os.path.join(meipass, "whispr", "core",
                                      "transcribe_worker.py"))
    except Exception:
        pass
    try:
        exe_dir = os.path.dirname(os.path.abspath(sys.executable))
        cands.append(os.path.join(exe_dir, "_internal", "whispr", "core",
                                  "transcribe_worker.py"))
    except Exception:
        pass
    for c in cands:
        if os.path.exists(c):
            return c
    return None


def find_system_python_with_whisper() -> str | None:
    """
    Find a real Python install that already has faster-whisper. Used as a
    fallback when the bundled worker cannot initialise its native libraries
    on this machine — a working slow path beats a crash.
    """
    cands = []
    if os.name == "nt":
        for var in ("LOCALAPPDATA", "PROGRAMFILES"):
            base = os.environ.get(var)
            if not base:
                continue
            progs = os.path.join(base, "Programs", "Python")
            for root in (progs, base):
                if os.path.isdir(root):
                    try:
                        for d in os.listdir(root):
                            if d.lower().startswith("python"):
                                p = os.path.join(root, d, "python.exe")
                                if os.path.exists(p):
                                    cands.append(p)
                    except Exception:
                        pass
        for p in ("python.exe", "py.exe"):
            found = shutil.which(p)
            if found:
                cands.append(found)
    else:
        for p in ("python3", "python"):
            found = shutil.which(p)
            if found:
                cands.append(found)

    seen = set()
    for p in cands:
        if p in seen:
            continue
        seen.add(p)
        try:
            r = subprocess.run(
                [p, "-c", "import faster_whisper,sys;sys.stdout.write('yes')"],
                capture_output=True, text=True, timeout=30,
                creationflags=_CREATE_NO_WINDOW)
            if r.returncode == 0 and "yes" in (r.stdout or ""):
                return p
        except Exception:
            continue
    return None


class WorkerError(RuntimeError):
    pass


class Transcriber:
    def __init__(self, model_size="distil-small.en", device="auto",
                 compute_type="int8", language="en", download_root=None,
                 engine_python=None, cpu_threads=0, beam_size=1):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self.language = language
        self.download_root = download_root
        self.engine_python = engine_python
        self.cpu_threads = cpu_threads
        self.beam_size = beam_size

        self._proc = None
        self._replies: "queue.Queue[dict]" = queue.Queue()
        self._lock = threading.RLock()
        self._next_id = 1
        self._model_loaded = False
        self._stderr_tail: list[str] = []
        self.active_model = None
        self._native_crashes = 0
        self._use_fallback = False
        # optional callback(str) for UI status ("Downloading speech model…")
        self.on_status = None

    # ------------------------------------------------------------ status
    @property
    def loaded(self) -> bool:
        return self._model_loaded

    def _status(self, msg: str):
        log(f"transcriber: {msg}")
        cb = self.on_status
        if cb:
            try:
                cb(msg)
            except Exception:
                pass

    # ------------------------------------------------------------ process
    def _cmd(self) -> list[str]:
        args = [self.model_size, self.language, self.download_root or "",
                self.compute_type or "int8", str(self.cpu_threads or 0)]

        # An explicitly configured interpreter always wins.
        if self.engine_python and os.path.exists(self.engine_python):
            worker = _worker_script_path()
            if worker:
                return [self.engine_python, worker] + args
            log("engine_python set but transcribe_worker.py not found")

        # If the bundled worker has already proved it crashes on this machine,
        # go straight to the system-Python fallback instead of crashing again.
        if self._use_fallback:
            py = find_system_python_with_whisper()
            if py:
                worker = _worker_script_path()
                if worker:
                    log(f"using system Python fallback: {py}")
                    return [py, worker] + args
            raise WorkerError(
                "The bundled speech engine crashes on this machine, and no "
                "system Python with faster-whisper was found. Install it with:"
                "  pip install faster-whisper")

        if getattr(sys, "frozen", False):
            exe = find_worker_exe()
            if not exe:
                raise WorkerError(
                    "VoxKey-Whisper.exe is missing from the installation "
                    "folder — reinstall VoxKey.")
            return [exe] + args
        return [sys.executable, "-m", "whispr.core.transcribe_worker"] + args

    def _drain_stdout(self, proc):
        try:
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    log(f"worker stdout (unparsed): {line}")
                    continue
                ev = msg.get("event")
                if ev == "loading":
                    self._status("Loading speech model — first run downloads "
                                 "it, this can take a few minutes…")
                elif ev == "loaded":
                    self._model_loaded = True
                    got, ct = msg.get("model"), msg.get("compute_type")
                    self.active_model = got
                    if got and got != self.model_size:
                        self._status(
                            f"'{self.model_size}' would not load on this "
                            f"machine — running the '{got}' model instead "
                            f"({ct}). Dictation works; accuracy is lower.")
                    else:
                        self._status("Speech model ready.")
                elif ev == "ready":
                    log("worker process ready")
                else:
                    self._replies.put(msg)
        except Exception as e:
            log_exception("worker stdout reader", e)
        finally:
            self._replies.put({"__eof__": True})

    def _drain_stderr(self, proc):
        try:
            for line in proc.stderr:
                line = line.rstrip()
                if not line:
                    continue
                self._stderr_tail.append(line)
                del self._stderr_tail[:-40]
                log(f"[whisper-worker] {line}")
        except Exception:
            pass

    def _ensure_worker(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        self._model_loaded = False
        self._stderr_tail = []
        # flush any stale replies from a dead worker
        try:
            while True:
                self._replies.get_nowait()
        except queue.Empty:
            pass

        cmd = self._cmd()
        log(f"starting whisper worker: {cmd}")
        env = dict(os.environ)
        # Strip PyInstaller's own bootloader variables: inherited by a child
        # exe they make it load the PARENT's bundle and fail weirdly.
        for k in list(env):
            if k.startswith("_PYI") or k in ("_MEIPASS2",):
                env.pop(k, None)
        env.setdefault("PYTHONUNBUFFERED", "1")
        env.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
        env.setdefault("OMP_NUM_THREADS", "1")
        self._proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", bufsize=1, env=env,
            creationflags=_CREATE_NO_WINDOW,
            # NOT the install dir: Program Files is read-only, and some
            # libraries try to write scratch files into the working directory.
            cwd=tempfile.gettempdir(),
        )
        log(f"worker pid {self._proc.pid}")
        threading.Thread(target=self._drain_stdout, args=(self._proc,),
                         daemon=True).start()
        threading.Thread(target=self._drain_stderr, args=(self._proc,),
                         daemon=True).start()

    # Windows exit codes that tell us what actually killed the worker.
    _EXIT_MEANINGS = {
        0xC0000005: "ACCESS VIOLATION — a native crash inside the speech "
                    "library (usually ctranslate2 hitting an unsupported CPU "
                    "instruction path).",
        0xC0000135: "MISSING DLL — a required library was not bundled into "
                    "the installation folder.",
        0xC0000142: "DLL INITIALISATION FAILED — a bundled library could not "
                    "start up.",
        0xC000007B: "BAD IMAGE FORMAT — a 32/64-bit library mismatch.",
        0xC00000FD: "STACK OVERFLOW inside the speech library.",
    }

    _last_exit_was_native_crash = False

    def _exit_diagnosis(self) -> str:
        p = self._proc
        if p is None:
            return ""
        rc = p.poll()
        if rc is None or rc == 0:
            return ""
        code = rc & 0xFFFFFFFF
        self._last_exit_was_native_crash = code in (
            0xC0000005, 0xC0000142, 0xC000007B, 0xC0000135)
        meaning = self._EXIT_MEANINGS.get(code)
        out = f" Worker exit code 0x{code:08X} ({rc})."
        if meaning:
            out += " " + meaning
        # a native crash leaves a C-level traceback behind
        try:
            base = os.environ.get("APPDATA") or os.path.expanduser("~")
            fp = os.path.join(base, "VoxKey", "worker_fault.log")
            if os.path.exists(fp) and os.path.getsize(fp) > 0:
                txt = open(fp, encoding="utf-8", errors="replace").read()
                log("---- worker native crash traceback ----")
                for line in txt.splitlines()[:40]:
                    log(f"    {line}")
                log("---- end crash traceback ----")
                out += " A native crash traceback was written to worker_fault.log."
        except Exception:
            pass
        return out

    def _stderr_hint(self) -> str:
        tail = " | ".join(self._stderr_tail[-3:])
        hint = f" Details: {tail}" if tail else ""
        return hint + self._exit_diagnosis()

    def _request(self, payload: dict, timeout: float) -> dict:
        """Send one request, wait for its reply. Restarts a dead worker once."""
        with self._lock:
            for attempt in (1, 2):
                try:
                    self._ensure_worker()
                    rid = self._next_id
                    self._next_id += 1
                    payload = dict(payload, id=rid)
                    self._proc.stdin.write(json.dumps(payload) + "\n")
                    self._proc.stdin.flush()

                    deadline_q = self._replies
                    while True:
                        msg = deadline_q.get(timeout=timeout)
                        if msg.get("__eof__"):
                            raise WorkerError(
                                "the speech engine stopped unexpectedly."
                                + self._stderr_hint())
                        if msg.get("id") == rid:
                            return msg
                        # stale reply from an earlier request — ignore
                except queue.Empty:
                    self._kill()
                    raise WorkerError("the speech engine timed out.")
                except (BrokenPipeError, OSError, WorkerError) as e:
                    crashed = self._last_exit_was_native_crash
                    self._kill()
                    if crashed and not self._use_fallback:
                        # A 0xC0000005 means the bundled worker's libraries
                        # cannot initialise here. Retrying it identically will
                        # crash identically, so switch strategy instead.
                        self._native_crashes += 1
                        if find_system_python_with_whisper():
                            log("bundled worker crashed natively — switching "
                                "to the system Python fallback")
                            self._use_fallback = True
                            continue
                    if attempt == 2:
                        raise WorkerError(str(e)) from e
                    log(f"worker failed ({e}); restarting and retrying once")
            raise WorkerError("the speech engine could not be started.")

    def _kill(self):
        p, self._proc = self._proc, None
        self._model_loaded = False
        if p is None:
            return
        try:
            p.kill()
        except Exception:
            pass

    # ------------------------------------------------------------ API
    def load(self):
        """Start the worker and load the model NOW (call on a background
        thread at startup so the first dictation is instant)."""
        try:
            r = self._request({"cmd": "load"}, timeout=3600)
            if not r.get("ok"):
                raise WorkerError(r.get("error", "unknown error"))
            self._model_loaded = True
        except Exception as e:
            log_exception("model preload", e)
            raise

    def shutdown(self):
        with self._lock:
            p = self._proc
            if p and p.poll() is None:
                try:
                    p.stdin.write(json.dumps({"cmd": "quit"}) + "\n")
                    p.stdin.flush()
                    p.wait(timeout=3)
                except Exception:
                    pass
            self._kill()

    def transcribe(self, audio: np.ndarray, initial_prompt: str = "") -> str:
        if audio is None or len(audio) == 0:
            return ""

        d = tempfile.mkdtemp(prefix="voxkey_")
        ap = os.path.join(d, "audio.npy")
        try:
            np.save(ap, np.asarray(audio, dtype=np.float32))
            r = self._request({"cmd": "transcribe", "audio": ap,
                               "prompt": initial_prompt or "",
                               "lang": self.language,
                               "beam_size": self.beam_size or 1},
                              timeout=600)
            if not r.get("ok"):
                raise WorkerError(r.get("error", "transcription failed"))
            return _clean(r.get("text", ""))
        finally:
            shutil.rmtree(d, ignore_errors=True)


def _clean(text: str) -> str:
    import re
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return text
