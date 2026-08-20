"""
transcribe_worker.py — runs Whisper in a SEPARATE, LONG-LIVED PROCESS.

Why a separate process: faster-whisper/ctranslate2 can crash natively (access
violation) on some Windows CPUs. A crash here kills only the worker, not VoxKey.

Why long-lived: the old design spawned a fresh process per dictation, so the
model was loaded from disk EVERY time (5-20s of dead air before any text
appeared). Now the process starts once, loads the model once, and then answers
requests in a fraction of a second.

Protocol — one JSON object per line.
  stdin  : {"id": 1, "cmd": "load"}
           {"id": 2, "cmd": "transcribe", "audio": "...audio.npy",
            "prompt": "...", "lang": "en"}
           {"cmd": "quit"}
  stdout : {"event": "ready"}                     (worker booted)
           {"event": "loading"} / {"event": "loaded"}
           {"id": 2, "ok": true, "text": "hello world"}
           {"id": 2, "ok": false, "error": "..."}

Everything human-readable goes to stderr (the parent drains it into the log),
so stdout stays a clean protocol channel.

Usage: transcribe_worker <model_size> <language> [download_root] [compute_type]
                         [cpu_threads]
"""
import sys
import os
import json
import traceback


# We talk over RAW FILE DESCRIPTORS, not sys.stdout/sys.stderr. A windowed
# PyInstaller exe can have sys.stdout set to None or to a null writer, which
# would silently swallow every reply — fd 1 is always the real pipe.
def _emit(obj):
    try:
        os.write(1, (json.dumps(obj) + "\n").encode("utf-8"))
    except Exception:
        pass


def _err(msg):
    try:
        os.write(2, (str(msg) + "\n").encode("utf-8", "replace"))
    except Exception:
        pass


class _StderrStream:
    """Anything a library prints goes to stderr, never onto the protocol pipe."""
    def write(self, s):
        if s:
            _err(s.rstrip("\n"))
        return len(s or "")

    def flush(self):
        pass

    def isatty(self):
        return False


def _stdin_lines():
    """Line-by-line reader over fd 0 (works with no sys.stdin in a GUI exe)."""
    buf = b""
    while True:
        try:
            chunk = os.read(0, 65536)
        except Exception:
            return
        if not chunk:
            return
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.decode("utf-8", "replace")


def _arm_faulthandler():
    """
    ctranslate2 can die with a raw access violation, which Python cannot
    catch. faulthandler writes a C-level traceback at the moment of death,
    which is the only way to see WHERE it crashed.
    """
    try:
        import faulthandler
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        d = os.path.join(base, "VoxKey")
        os.makedirs(d, exist_ok=True)
        f = open(os.path.join(d, "worker_fault.log"), "w", encoding="utf-8")
        faulthandler.enable(file=f, all_threads=True)
        return f
    except Exception:
        return None


def main():
    _fault = _arm_faulthandler()
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

    argv = sys.argv[1:]
    model_size = argv[0] if len(argv) > 0 else "small"
    language = argv[1] if len(argv) > 1 else "en"
    download_root = argv[2] if len(argv) > 2 and argv[2] else None
    want_compute = argv[3] if len(argv) > 3 and argv[3] else "int8"

    # cpu_threads: 0 / unset = auto. We were pinned to ONE thread as a crash
    # mitigation back when the 0xC0000005 was misdiagnosed as a threading
    # problem. It was actually a DLL collision, so there is no reason to
    # cripple the CPU — this alone is most of the speed-up.
    try:
        want_threads = int(argv[4]) if len(argv) > 4 and argv[4] else 0
    except ValueError:
        want_threads = 0
    if want_threads <= 0:
        cores = os.cpu_count() or 4
        # Leave a little headroom so the UI stays responsive while dictating.
        want_threads = max(1, min(cores - 1, 8)) if cores > 2 else 1
    os.environ["OMP_NUM_THREADS"] = str(want_threads)
    _err("using %d CPU threads" % want_threads)

    sys.stdout = _StderrStream()

    import numpy as np

    model = {"m": None}

    def ensure_model():
        if model["m"] is not None:
            return model["m"]
        _emit({"event": "loading", "model": model_size})

        from faster_whisper import WhisperModel

        # Try progressively more conservative configurations. int8 uses CPU
        # instructions that some machines mis-handle inside ctranslate2; float32
        # is slower but universally safe. A smaller model is the last resort so
        # the user gets *working* dictation rather than none.
        plans = [
            (model_size, want_compute),
            (model_size, "int8"),
            (model_size, "float32"),
            ("base", "int8"),
            ("base", "float32"),
            ("tiny", "float32"),
        ]
        seen, ordered = set(), []
        for pl in plans:
            if pl not in seen:
                seen.add(pl)
                ordered.append(pl)

        last = None
        for size, ctype in ordered:
            try:
                _err("attempting model=%s compute_type=%s (download_root=%s)"
                     % (size, ctype, download_root))
                kw = dict(device="cpu", compute_type=ctype,
                          cpu_threads=want_threads, num_workers=1)
                if download_root:
                    kw["download_root"] = download_root
                model["m"] = WhisperModel(size, **kw)
                _emit({"event": "loaded", "model": size, "compute_type": ctype})
                _err("model loaded: %s / %s" % (size, ctype))
                return model["m"]
            except Exception as e:
                last = e
                _err("FAILED model=%s compute_type=%s -> %s: %s"
                     % (size, ctype, type(e).__name__, e))
                _err(traceback.format_exc())
        raise RuntimeError("no model configuration would load: %s" % last)

    _emit({"event": "ready", "pid": os.getpid()})

    for line in _stdin_lines():
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue

        rid = req.get("id")
        cmd = req.get("cmd", "transcribe")

        if cmd == "quit":
            break

        try:
            if cmd == "load":
                ensure_model()
                _emit({"id": rid, "ok": True, "text": ""})
                continue

            if cmd == "transcribe":
                m = ensure_model()
                audio = np.load(req["audio"])
                prompt = req.get("prompt") or None
                lang = req.get("lang") or language
                beam = int(req.get("beam_size") or 1)

                segments, _info = m.transcribe(
                    audio,
                    language=lang,
                    # beam_size=1 is greedy decoding. For short dictation
                    # utterances it is several times faster than beam 5 and
                    # the accuracy difference is negligible — beam search
                    # mainly helps on long, ambiguous audio.
                    beam_size=beam,
                    # Silero VAD inside faster-whisper is far better than an
                    # energy threshold at finding speech boundaries. Padding
                    # stops it clipping quiet word onsets, which was making
                    # the first word go missing.
                    vad_filter=True,
                    vad_parameters=dict(
                        min_silence_duration_ms=500,
                        speech_pad_ms=400,
                        threshold=0.35,
                    ),
                    initial_prompt=prompt,
                    condition_on_previous_text=False,
                    # Retry with a little randomness only if greedy decoding
                    # produces something degenerate, rather than always.
                    temperature=[0.0, 0.2, 0.4],
                    compression_ratio_threshold=2.4,
                    log_prob_threshold=-1.0,
                    no_speech_threshold=0.6,
                    # Trims the trailing/leading silence Whisper sometimes
                    # hallucinates words into.
                    without_timestamps=True,
                )
                text = " ".join(s.text.strip() for s in segments).strip()
                _emit({"id": rid, "ok": True, "text": text})
                continue

            _emit({"id": rid, "ok": False, "error": "unknown command %r" % cmd})

        except Exception as e:
            _err(traceback.format_exc())
            _emit({"id": rid, "ok": False,
                   "error": "%s: %s" % (type(e).__name__, e)})


if __name__ == "__main__":
    main()
