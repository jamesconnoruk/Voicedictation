"""
config.py — persistent application settings.

Stored as JSON in the user's config dir. Everything the user can change
in Settings lives here. Safe defaults so the app runs on first launch
with no setup.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
from dataclasses import dataclass, asdict, field


def _default_config_dir() -> Path:
    """Return the per-OS config directory for the app."""
    if os.name == "nt":  # Windows
        base = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
    elif os.sys.platform == "darwin":  # macOS
        base = str(Path.home() / "Library" / "Application Support")
    else:  # Linux / other
        base = os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))
    return Path(base) / "VoxKey"


@dataclass
class Config:
    # --- Hotkey ---
    # A pynput-style combination string, e.g. "<ctrl>+<shift>".
    # Held = record. Released = transcribe + paste.
    hotkey: str = "<ctrl>+<shift>"

    # --- Speech engine (local faster-whisper) ---
    # English-only models (the ".en" suffix) are BOTH more accurate and faster
    # than the multilingual ones for English speech — the multilingual weights
    # spend capacity on 98 other languages. distil-* variants are faster again
    # for a small accuracy cost.
    model_size: str = "distil-small.en"
    device: str = "auto"               # auto/cpu/cuda
    compute_type: str = "int8"         # int8 (cpu) / float16 (gpu)
    # 0 = auto (cores - 1, capped at 8). Was pinned to 1 as a crash mitigation.
    cpu_threads: int = 0
    # 1 = greedy. Higher searches more candidates: slower, rarely better for
    # short dictation.
    beam_size: int = 1
    language: str = "en"

    # --- Microphone / VAD ---
    # Prefer matching by NAME: PortAudio indices shift whenever a USB device
    # is plugged/unplugged or a driver updates, so a saved index silently
    # becomes a different (often silent) device. The name is resolved first,
    # the index is only a hint.
    input_device_name: str | None = None
    input_device_index: int | None = None   # None = system default
    overlay_x: int | None = None             # remembered overlay position
    overlay_y: int | None = None
    color_scheme: str = "default"            # default/neon_pink/electric_blue/xp/deep_red/neon_green
    sample_rate: int = 16000                 # whisper wants 16kHz
    silence_threshold: float = 0.015         # RMS below this = silence (tuned by calibration)
    mic_gain: float = 1.0                    # manual multiplier (only used when auto_gain is off)
    # Automatically bring speech to the level Whisper expects. Far more robust
    # than a fixed multiplier — handles leaning in, leaning back, quiet mics.
    auto_gain: bool = True
    auto_gain_target: float = 0.08
    vad_enabled: bool = True

    # --- Localisation ---
    british_english: bool = True       # normalise output to UK spelling + accent prompt

    # --- Behaviour ---
    autostart: bool = False            # launch on Windows login
    # "paste" = copy then press Ctrl+V for you (the Wispr behaviour)
    # "type"  = simulate the keystrokes directly
    # "copy"  = copy only; you press Ctrl+V yourself
    paste_method: str = "paste"
    play_sounds: bool = True           # start/stop earcons
    min_record_seconds: float = 0.3    # ignore accidental taps shorter than this

    # Explicit path to a Python interpreter that has faster-whisper installed.
    # Used instead of the bundled worker when set — the escape hatch for
    # machines where the frozen worker's native libraries won't initialise.
    engine_python: str | None = None

    # --- Learned data lives in separate files, but we track versions here ---
    vocabulary_version: int = 0

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or (_default_config_dir() / "config.json")
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                # only keep keys we know about (forward/backward compatible)
                known = {f for f in cls.__dataclass_fields__}
                filtered = {k: v for k, v in data.items() if k in known}
                return cls(**filtered)
            except (json.JSONDecodeError, TypeError):
                # corrupt config — start fresh but don't crash
                return cls()
        return cls()

    def save(self, path: Path | None = None) -> None:
        path = path or (_default_config_dir() / "config.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def config_dir() -> Path:
    d = _default_config_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d
