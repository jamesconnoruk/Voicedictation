#!/usr/bin/env python3
r"""
voxkey_tune.py — measure real speed on THIS CPU and apply the best settings.

You said dictation is slow. Rather than guessing, this times the actual
combinations on your machine and writes the winner into config.json.

What it measures, per configuration:
  * load time      — one-off at startup, doesn't affect per-dictation speed
  * RTF            — Real-Time Factor: seconds of CPU per second of speech.
                     RTF 0.5 means a 10-second sentence takes 5 seconds.
                     Under ~0.4 feels responsive. Over 1.0 feels broken.

It records a short sample of YOUR voice and runs every configuration against
it, so the numbers reflect your microphone, your accent and your CPU — not a
synthetic clip.

    python voxkey_tune.py            # record a sample, then benchmark
    python voxkey_tune.py --quick    # fewer configurations
"""
from __future__ import annotations

import os
import sys
import json
import time
import shutil
import argparse
import tempfile
import subprocess
from datetime import datetime

IS_WIN = os.name == "nt"
CREATE_NO_WINDOW = 0x08000000 if IS_WIN else 0
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_PATH = os.path.join(HERE, "voxkey_tune_log.txt")
_LINES: list[str] = []


def log(m=""):
    _LINES.append(str(m))
    print(m, flush=True)


def save():
    try:
        open(LOG_PATH, "w", encoding="utf-8").write("\n".join(_LINES) + "\n")
    except Exception:
        pass


def step(t):
    log("")
    log("=" * 70)
    log(t)
    log("=" * 70)


def config_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "VoxKey", "config.json")


def load_config() -> dict:
    try:
        return json.loads(open(config_path(), encoding="utf-8").read())
    except Exception:
        return {}


# --------------------------------------------------------------- recording
SAMPLE_SECONDS = 8.0
PROMPT_TEXT = ("The quick brown fox jumps over the lazy dog. "
               "Please send the invoice to the warehouse before five o'clock. "
               "My order number is four seven two nine.")


def record_sample(cfg: dict) -> str | None:
    import numpy as np
    import sounddevice as sd
    SILENT = ("stream mix", "stereo mix", "sound mapper",
              "primary sound capture", "loopback", "pc speaker")

    dev = cfg.get("input_device_index")
    name = cfg.get("input_device_name")

    # Don't blindly trust a saved index — they shift when USB gear is
    # re-plugged, and "system default" on this machine is a silent GoXLR
    # loopback. Resolve by name, and refuse known-silent endpoints.
    SILENT = ("stream mix", "stereo mix", "sound mapper",
              "primary sound capture", "loopback", "pc speaker")
    try:
        devs = sd.query_devices()
        ins = [(i, d.get("name", "")) for i, d in enumerate(devs)
               if d.get("max_input_channels", 0) > 0]
        resolved = None
        if name:
            for i, n in ins:
                if n.strip() == name.strip():
                    resolved = i
                    break
            if resolved is None:
                for i, n in ins:
                    if name.strip().lower() in n.lower():
                        resolved = i
                        break
        if resolved is None and dev is not None:
            resolved = dev
        if resolved is None:
            for i, n in ins:
                if not any(h in n.lower() for h in SILENT):
                    resolved = i
                    log(f"  no microphone configured — using {n!r}")
                    break
        if resolved != dev and resolved is not None:
            log(f"  saved index {dev} didn't match; using index {resolved}")
        dev = resolved
        name = dict(ins).get(dev, name)
    except Exception as e:
        log(f"  could not resolve device ({e})")

    log(f"  microphone: {name or 'system default'} (index {dev})")
    if name and any(h in name.lower() for h in SILENT):
        log("  WARNING: that is a loopback/virtual endpoint. It will record")
        log("  silence. Run AUTOFIX.bat to pick a real microphone first.")

    rate = 48000
    try:
        info = sd.query_devices(dev if dev is not None else None, "input")
        rate = int(info.get("default_samplerate") or 48000)
    except Exception:
        pass

    log("")
    log("  Read this aloud, at your normal dictation pace:")
    log("")
    log(f"    \"{PROMPT_TEXT}\"")
    log("")
    try:
        input(f"  Press ENTER, then start reading ({SAMPLE_SECONDS:.0f}s)… ")
    except (EOFError, KeyboardInterrupt):
        return None

    frames = []
    try:
        with sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                            device=dev,
                            callback=lambda i, n, t, s: frames.append(i.copy())):
            for i in range(int(SAMPLE_SECONDS)):
                print(f"    recording… {int(SAMPLE_SECONDS) - i}s ", end="\r",
                      flush=True)
                time.sleep(1)
    except Exception as e:
        log(f"  could not record: {e}")
        return None
    print(" " * 40, end="\r")

    if not frames:
        log("  no audio captured")
        return None

    audio = np.concatenate([f[:, 0] if f.ndim > 1 else f
                            for f in frames]).astype(np.float32)
    # Match the real pipeline: auto-gain, not the old fixed multiplier.
    mag = np.abs(audio)
    loud = np.percentile(mag, 90)
    voiced = audio[mag >= max(loud * 0.35, 1e-6)]
    if voiced.size > 16:
        vr = float(np.sqrt(np.mean(voiced.astype(np.float64) ** 2)))
        if vr > 1e-7:
            g = min(30.0, 0.08 / vr)
            pk = float(np.percentile(mag, 99.9))
            if pk * g > 0.95:
                g = 0.95 / max(pk, 1e-6)
            audio = np.clip(audio * g, -1.0, 1.0).astype(np.float32)
            log(f"  auto-gain applied: {g:.2f}x")

    # resample to 16 kHz, as the real pipeline does
    if rate != 16000:
        n = int(round(len(audio) / rate * 16000))
        audio = np.interp(np.linspace(0, len(audio) - 1, n),
                          np.arange(len(audio)), audio).astype(np.float32)

    rms = float(np.sqrt(np.mean(audio.astype(np.float64) ** 2)))
    log(f"  captured {len(audio)/16000:.1f}s of audio, RMS {rms:.4f}")

    # Benchmarking silence produces a meaningless six-way tie at 0% and would
    # write a "winner" chosen at random. Refuse outright.
    if rms < 0.0015:
        log("")
        log("  *** NO AUDIO WAS CAPTURED (RMS is essentially zero). ***")
        log("  Benchmarking this would compare six models transcribing")
        log("  silence, so nothing will be measured or changed.")
        log("")
        log("  Fix the microphone first:")
        log("    1. Run AUTOFIX.bat — it finds a device that works and")
        log("       writes it into config.json.")
        log("    2. Then run this again.")
        return None
    if rms < 0.006:
        log("  WARNING: very quiet. Auto-gain will compensate at dictation")
        log("  time, but consider moving closer to the microphone.")

    path = os.path.join(tempfile.mkdtemp(prefix="voxkey_tune_"), "sample.npy")
    __import__("numpy").save(path, audio)
    return path


# -------------------------------------------------------------- benchmark
BENCH_SRC = r'''
import sys, json, time, os
import numpy as np
from faster_whisper import WhisperModel
model, ctype, threads, beam, audio_path = sys.argv[1:6]
threads, beam = int(threads), int(beam)
os.environ["OMP_NUM_THREADS"] = str(threads)
audio = np.load(audio_path)
t0 = time.time()
m = WhisperModel(model, device="cpu", compute_type=ctype,
                 cpu_threads=threads, num_workers=1)
load = time.time() - t0
t1 = time.time()
segs, _ = m.transcribe(audio, language="en", beam_size=beam, vad_filter=True,
                       vad_parameters=dict(min_silence_duration_ms=500,
                                           speech_pad_ms=400, threshold=0.35),
                       condition_on_previous_text=False,
                       temperature=[0.0, 0.2, 0.4], without_timestamps=True)
text = " ".join(s.text.strip() for s in segs).strip()
infer = time.time() - t1
print("BENCH " + json.dumps({"load": round(load, 1), "infer": round(infer, 2),
                             "audio_s": round(len(audio)/16000, 2),
                             "text": text}))
'''


def run_bench(py, model, ctype, threads, beam, audio_path):
    src = os.path.join(HERE, "_bench.py")
    open(src, "w", encoding="utf-8").write(BENCH_SRC)
    try:
        r = subprocess.run([py, src, model, ctype, str(threads), str(beam),
                            audio_path],
                           capture_output=True, text=True, timeout=2400,
                           encoding="utf-8", errors="replace",
                           creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        return None, "timed out"
    finally:
        try:
            os.remove(src)
        except Exception:
            pass
    line = next((l for l in r.stdout.splitlines() if l.startswith("BENCH ")), None)
    if line:
        return json.loads(line[6:]), None
    code = r.returncode & 0xFFFFFFFF
    tail = [l for l in (r.stderr or "").splitlines() if l.strip()][-2:]
    return None, f"exit 0x{code:08X} " + " | ".join(tail)


def similarity(a: str, b: str) -> float:
    """Rough word-overlap score against the prompt, 0..1."""
    import re
    from difflib import SequenceMatcher
    norm = lambda s: re.sub(r"[^a-z0-9 ]", "", s.lower()).split()
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    args = ap.parse_args()

    log("=" * 70)
    log("VoxKey Tune — measure and apply the best settings for this CPU")
    log("=" * 70)
    log(f"Time  : {datetime.now():%Y-%m-%d %H:%M:%S}")
    log(f"Cores : {os.cpu_count()}")

    cfg = load_config()
    py = cfg.get("engine_python") or sys.executable
    log(f"Python: {py}")

    try:
        subprocess.run([py, "-c", "import faster_whisper"], check=True,
                       capture_output=True, timeout=60,
                       creationflags=CREATE_NO_WINDOW)
    except Exception:
        log("\nfaster-whisper isn't installed in that Python.")
        log(f"Run:  \"{py}\" -m pip install faster-whisper")
        save()
        input("\nPress ENTER to close… ")
        return

    step("[1] RECORDING A SAMPLE OF YOUR VOICE")
    audio_path = record_sample(cfg)
    if not audio_path:
        log("  no sample — cannot benchmark")
        save()
        input("\nPress ENTER to close… ")
        return

    step("[2] BENCHMARKING")
    cores = os.cpu_count() or 4
    threads = max(1, min(cores - 1, 8))

    if args.quick:
        combos = [("distil-small.en", "int8", threads, 1),
                  ("small.en", "int8", threads, 1)]
    else:
        combos = [
            ("base.en", "int8", threads, 1),
            ("distil-small.en", "int8", threads, 1),
            ("small.en", "int8", threads, 1),
            ("small.en", "int8", threads, 5),
            ("distil-medium.en", "int8", threads, 1),
            ("small", "int8", 1, 5),      # the old settings, for comparison
        ]

    log(f"  using {threads} threads (of {cores} cores) unless stated")
    log("  RTF = seconds of compute per second of speech. Lower is better.")
    log("")
    log(f"  {'model':<20} {'thr':>3} {'beam':>4} {'load':>6} {'infer':>7} "
        f"{'RTF':>6}  {'match':>6}")
    log("  " + "-" * 66)

    results = []
    for model, ctype, thr, beam in combos:
        data, err = run_bench(py, model, ctype, thr, beam, audio_path)
        if err:
            log(f"  {model:<20} {thr:>3} {beam:>4}   FAILED — {err}")
            continue
        rtf = data["infer"] / max(0.01, data["audio_s"])
        match = similarity(PROMPT_TEXT, data["text"])
        log(f"  {model:<20} {thr:>3} {beam:>4} {data['load']:>5.1f}s "
            f"{data['infer']:>6.2f}s {rtf:>6.2f} {match*100:>5.0f}%")
        results.append({"model": model, "compute": ctype, "threads": thr,
                        "beam": beam, "rtf": rtf, "match": match,
                        "text": data["text"], "load": data["load"]})

    # If every configuration scored ~0, the audio was unusable and the
    # "winner" would be arbitrary. Never write settings from that.
    if results and max(r["match"] for r in results) < 0.25:
        step("NOT APPLYING ANYTHING")
        log("  Every configuration scored under 25%, which means the audio")
        log("  didn't contain intelligible speech — not that the models are")
        log("  bad. Your settings have been left exactly as they were.")
        log("")
        log("  Run AUTOFIX.bat to sort the microphone out, then try again.")
        shutil.rmtree(os.path.dirname(audio_path), ignore_errors=True)
        save()
        input("\nPress ENTER to close… ")
        return

    if not results:
        log("\n  Everything failed — send me voxkey_tune_log.txt")
        save()
        input("\nPress ENTER to close… ")
        return

    log("")
    log("  What each configuration heard:")
    for r in results:
        log(f"    [{r['model']} b{r['beam']}] {r['text'][:100]}")

    step("[3] VERDICT")
    # Prefer accurate; among those within 3% of the best, take the fastest.
    best_match = max(r["match"] for r in results)
    good = [r for r in results if r["match"] >= best_match - 0.03]
    good.sort(key=lambda r: r["rtf"])
    win = good[0]

    log(f"  Best accuracy seen : {best_match*100:.0f}%")
    log(f"  Fastest at that accuracy: {win['model']}, beam {win['beam']}, "
        f"{win['threads']} threads")
    log(f"  RTF {win['rtf']:.2f} — a 10-second sentence takes about "
        f"{win['rtf']*10:.1f}s")

    old = next((r for r in results if r["model"] == "small"
                and r["threads"] == 1), None)
    if old:
        log("")
        log(f"  Old settings (small, 1 thread, beam 5): RTF {old['rtf']:.2f}")
        if win["rtf"] > 0:
            log(f"  That is {old['rtf']/win['rtf']:.1f}x slower than the "
                f"new pick.")

    step("[4] APPLYING")
    path = config_path()
    if os.path.exists(path):
        bak = path.replace(".json", f".backup-{datetime.now():%Y%m%d-%H%M%S}.json")
        shutil.copy2(path, bak)
        log(f"  backup: {bak}")
    cfg["model_size"] = win["model"]
    cfg["compute_type"] = win["compute"]
    cfg["cpu_threads"] = win["threads"]
    cfg["beam_size"] = win["beam"]
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        json.dump(cfg, open(path, "w", encoding="utf-8"), indent=2)
        log(f"  written: {path}")
        for k in ("model_size", "compute_type", "cpu_threads", "beam_size"):
            log(f"    {k:<14}= {cfg[k]}")
    except Exception as e:
        log(f"  could not write: {e}")

    log("")
    log("  Restart VoxKey to pick this up.")
    log(f"  Log: {LOG_PATH}")
    shutil.rmtree(os.path.dirname(audio_path), ignore_errors=True)
    save()
    input("\nPress ENTER to close… ")


if __name__ == "__main__":
    main()
