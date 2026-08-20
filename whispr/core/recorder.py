"""
recorder.py — microphone capture + level metering + VAD trimming.

Runtime capture uses sounddevice (PortAudio). But the *signal maths* — RMS
level for the waveform, gain, silence trimming — is pure numpy and fully
testable without a mic. That maths is what makes "voice detection really good",
so it's separated out and tested.

Pipeline while the hotkey is held:
    callback pushes raw frames -> ring buffer
    each frame's RMS is exposed for the waveform overlay
On release:
    concatenate frames -> apply gain -> trim leading/trailing silence
    -> hand 16kHz float32 mono to the transcriber.
"""
from __future__ import annotations
import numpy as np
from collections import deque


def rms_level(frame: np.ndarray) -> float:
    """Root-mean-square amplitude of a float32 frame in [-1, 1]. -> [0, ~1]."""
    if frame.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))


def apply_gain(samples: np.ndarray, gain: float) -> np.ndarray:
    """Multiply by gain and hard-clip to [-1, 1] to avoid wrap distortion."""
    if gain == 1.0:
        return samples
    return np.clip(samples * gain, -1.0, 1.0).astype(np.float32)


def normalize_audio(samples: np.ndarray, target_rms: float = 0.08,
                   max_gain: float = 30.0, peak_ceiling: float = 0.95
                   ) -> tuple[np.ndarray, float]:
    """
    Bring speech up to the level Whisper expects, automatically.

    A fixed mic_gain multiplier can't cope with the real world: sit closer and
    you clip, lean back and you vanish. This measures the loud part of what was
    actually said (the 90th percentile, so a cough or a door slam doesn't set
    the level) and scales to a target, then limits the peak so nothing
    distorts. Quiet mics get a big lift, loud ones get left alone.

    Returns (audio, gain_applied) — pure maths, unit testable.
    """
    if samples.size == 0:
        return samples, 1.0
    a = samples.astype(np.float32)

    # Level of the SPOKEN parts only: silence would drag a plain RMS down and
    # make us over-amplify the noise floor.
    mag = np.abs(a)
    loud_gate = np.percentile(mag, 90)
    voiced = a[mag >= max(loud_gate * 0.35, 1e-6)]
    if voiced.size < 16:
        voiced = a
    rms = float(np.sqrt(np.mean(voiced.astype(np.float64) ** 2)))
    if rms <= 1e-7:
        return a, 1.0

    gain = min(max_gain, target_rms / rms)

    # Headroom check uses the 99.9th percentile, NOT the absolute maximum: a
    # single transient (door slam, mic bump, keyboard clack) would otherwise
    # set the ceiling and leave the actual speech far too quiet. The handful
    # of samples above that level are clipped instead, which is inaudible.
    peak = float(np.percentile(mag, 99.9))
    if peak * gain > peak_ceiling:
        gain = peak_ceiling / max(peak, 1e-6)
    gain = max(gain, 0.05)

    out = np.clip(a * gain, -1.0, 1.0).astype(np.float32)
    return out, float(gain)


def dc_offset_removal(samples: np.ndarray) -> np.ndarray:
    """Strip any DC bias — some USB interfaces add one, and it eats headroom."""
    if samples.size == 0:
        return samples
    return (samples - float(np.mean(samples))).astype(np.float32)


def trim_silence(samples: np.ndarray, sample_rate: int, threshold: float,
                 frame_ms: int = 30, pad_ms: int = 120) -> np.ndarray:
    """
    Remove leading/trailing silence using a simple energy VAD.
    Keeps `pad_ms` of padding around the detected speech so we don't clip
    plosives/word onsets. Returns the trimmed array (may be empty if all silence).
    """
    if samples.size == 0:
        return samples
    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    n_frames = int(np.ceil(len(samples) / frame_len))
    # pad so reshape is clean
    padded = np.pad(samples, (0, n_frames * frame_len - len(samples)))
    frames = padded.reshape(n_frames, frame_len)
    energies = np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))
    voiced = np.where(energies >= threshold)[0]
    if voiced.size == 0:
        return np.array([], dtype=np.float32)
    pad_frames = max(0, int(pad_ms / frame_ms))
    start = max(0, voiced[0] - pad_frames)
    end = min(n_frames, voiced[-1] + 1 + pad_frames)
    start_sample = start * frame_len
    end_sample = min(len(samples), end * frame_len)
    return samples[start_sample:end_sample].astype(np.float32)


def has_speech(samples: np.ndarray, sample_rate: int, threshold: float,
               min_voiced_ms: int = 150, frame_ms: int = 30) -> bool:
    """True if there's at least `min_voiced_ms` of energy above threshold."""
    if samples.size == 0:
        return False
    frame_len = max(1, int(sample_rate * frame_ms / 1000))
    n_frames = max(1, len(samples) // frame_len)
    trimmed = samples[:n_frames * frame_len].reshape(n_frames, frame_len)
    energies = np.sqrt(np.mean(trimmed.astype(np.float64) ** 2, axis=1))
    voiced_ms = int(np.sum(energies >= threshold)) * frame_ms
    return voiced_ms >= min_voiced_ms


def list_input_devices():  # pragma: no cover  (needs sounddevice)
    """
    Return a list of (index, name) for available input devices, so the user can
    pick one in Voice Setup. Empty list if sounddevice/PortAudio is unavailable.
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
        try:
            hostapis = sd.query_hostapis()
        except Exception:
            hostapis = []
        out = []
        for i, d in enumerate(devices):
            if d.get("max_input_channels", 0) > 0:
                name = d.get("name", f"Device {i}")
                # append host API (WASAPI/MME/etc) so duplicates are clear
                api_idx = d.get("hostapi", None)
                if hostapis and api_idx is not None and api_idx < len(hostapis):
                    api = hostapis[api_idx].get("name", "")
                    if api:
                        name = f"{name}  [{api}]"
                out.append((i, name))
        return out
    except Exception:
        return []


# Windows exposes a pile of virtual "input" endpoints that open happily and
# then deliver pure silence — loopback mixes, sound mappers, muted vendor
# streams. Never auto-select one of these; they are the classic cause of
# "the mic looks fine but nothing is ever transcribed".
_SILENT_DEVICE_HINTS = (
    "stream mix", "stereo mix", "sound mapper", "primary sound capture",
    "wave out", "what u hear", "loopback", "pc speaker",
)


def looks_like_silent_endpoint(name: str) -> bool:
    """True if this device name is a known silent/virtual endpoint."""
    n = (name or "").lower()
    return any(h in n for h in _SILENT_DEVICE_HINTS)


def resolve_device(name: str | None = None, index: int | None = None):
    """
    Work out which PortAudio device to open, most trustworthy source first:

        1. exact name match   (survives re-plugging / index shuffles)
        2. partial name match
        3. the saved index, but only if it still looks like the same device
        4. the system default, but ONLY if it isn't a known silent endpoint
        5. the first real-looking input device

    Returns (index_or_None, reason_string) — the reason goes in the log so a
    wrong pick is obvious rather than mysterious.
    """
    try:
        import sounddevice as sd
        devices = sd.query_devices()
    except Exception as e:
        return index, f"device list unavailable ({e}); using saved index {index}"

    inputs = [(i, d.get("name", "")) for i, d in enumerate(devices)
              if d.get("max_input_channels", 0) > 0]
    if not inputs:
        return None, "no input devices at all"

    if name:
        for i, n in inputs:
            if n.strip() == name.strip():
                return i, f"exact name match: {n!r} (index {i})"
        key = name.strip().lower()
        for i, n in inputs:
            if key in n.lower() or n.lower() in key:
                return i, f"partial name match: {n!r} (index {i})"

    if index is not None:
        for i, n in inputs:
            if i == index:
                return i, f"saved index {i} ({n!r})"

    try:
        dflt = sd.default.device
        dflt = dflt[0] if isinstance(dflt, (list, tuple)) else dflt
        if dflt is not None and dflt >= 0:
            dname = dict(inputs).get(dflt, "")
            if not looks_like_silent_endpoint(dname):
                return dflt, f"system default ({dname!r})"
            # The system default is a silent virtual endpoint. Falling back to
            # it would record nothing, so skip it deliberately.
            for i, n in inputs:
                if not looks_like_silent_endpoint(n):
                    return i, (f"system default {dname!r} is a silent virtual "
                               f"endpoint — using {n!r} (index {i}) instead")
    except Exception:
        pass

    for i, n in inputs:
        if not looks_like_silent_endpoint(n):
            return i, f"first usable input: {n!r} (index {i})"
    return inputs[0][0], f"last resort: {inputs[0][1]!r}"


def default_input_index():  # pragma: no cover
    try:
        import sounddevice as sd
        dev = sd.default.device
        # sd.default.device is (input, output)
        if isinstance(dev, (list, tuple)):
            return dev[0]
        return dev
    except Exception:
        return None


def _resample_to_16k(audio: np.ndarray, src_rate: int) -> np.ndarray:
    """Linear-resample mono float32 audio from src_rate to 16000 Hz."""
    target = 16000
    if src_rate == target or audio.size == 0:
        return audio.astype(np.float32)
    duration = audio.shape[0] / float(src_rate)
    n_target = int(round(duration * target))
    if n_target <= 1:
        return audio.astype(np.float32)
    src_idx = np.linspace(0.0, audio.shape[0] - 1, num=n_target)
    out = np.interp(src_idx, np.arange(audio.shape[0]), audio)
    return out.astype(np.float32)


class Recorder:
    """
    Runtime mic capture. sounddevice is imported lazily so importing this module
    never fails on a headless/mic-less box (like a CI runner or this sandbox).

    Robust capture: we open the device at ITS OWN native sample rate and channel
    count. Many Windows mics reject a forced 16000 Hz mono stream and then
    deliver silence, so we adapt to the device and downmix + resample to 16000
    Hz mono for Whisper in stop().
    """
    def __init__(self, sample_rate=16000, device_index=None, gain=1.0,
                 max_seconds=120, device_name=None):
        self.sample_rate = sample_rate          # target rate for Whisper (16k)
        self.device_index = device_index
        self.device_name = device_name
        self.gain = gain
        self.max_frames = int(sample_rate * max_seconds)
        self._frames = deque()
        self._stream = None
        self._latest_level = 0.0
        self._recording = False
        self._capture_rate = sample_rate        # actual device rate (set on start)

    def _callback(self, indata, frames, time_info, status):  # pragma: no cover
        mono = indata[:, 0] if indata.ndim > 1 else indata
        mono = mono.astype(np.float32)
        self._latest_level = rms_level(apply_gain(mono, self.gain))
        self._frames.append(mono.copy())

    def _device_default_rate(self, sd, device):  # pragma: no cover
        """Get the device's own default sample rate (most reliable choice)."""
        try:
            info = sd.query_devices(device, "input")
            return int(info.get("default_samplerate", 44100))
        except Exception:
            try:
                info = sd.query_devices(sd.default.device[0], "input")
                return int(info.get("default_samplerate", 44100))
            except Exception:
                return 44100

    def _open_stream(self, sd, device, rate):  # pragma: no cover
        """Try to open an input stream; return it or raise."""
        try:
            s = sd.InputStream(samplerate=rate, channels=1, dtype="float32",
                               device=device, callback=self._callback, blocksize=0)
            s.start()
            return s
        except Exception:
            # let PortAudio choose channel count
            s = sd.InputStream(samplerate=rate, dtype="float32",
                               device=device, callback=self._callback, blocksize=0)
            s.start()
            return s

    def start(self):  # pragma: no cover  (needs a real mic)
        import sounddevice as sd
        self._frames.clear()
        self._latest_level = 0.0
        self._recording = True

        # Build a list of (device, rate) attempts, most-preferred first.
        # If the chosen device is invalid (PaErrorCode -9996), we fall back to
        # the system default device so recording always works with something.
        from .applog import log

        chosen, why = resolve_device(self.device_name, self.device_index)
        log(f"recorder: {why}")

        attempts = []
        if chosen is not None:
            # Native rate FIRST and always. Forcing 16 kHz on a Windows mic is
            # frequently accepted and then answered with silence.
            attempts.append((chosen, self._device_default_rate(sd, chosen)))
            attempts.append((chosen, 48000))
            attempts.append((chosen, 44100))
        # last-ditch fallbacks
        attempts.append((None, self._device_default_rate(sd, None)))
        attempts.append((None, 48000))
        attempts.append((None, 44100))

        last_err = None
        for device, rate in attempts:
            try:
                self._stream = self._open_stream(sd, device, rate)
                self._capture_rate = rate
                self._active_device = device
                log(f"recorder: capturing from device {device} at {rate} Hz")
                return
            except Exception as e:
                last_err = e
                continue
        # everything failed — surface the error
        self._recording = False
        raise last_err if last_err else RuntimeError("no usable microphone")

    def stop(self) -> np.ndarray:  # pragma: no cover
        self._recording = False
        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        if not self._frames:
            return np.array([], dtype=np.float32)
        audio = np.concatenate(list(self._frames))
        self._frames.clear()
        audio = apply_gain(audio, self.gain)
        # resample from the device's rate down to 16k for Whisper
        if self._capture_rate != self.sample_rate:
            audio = _resample_to_16k(audio, self._capture_rate)
        return audio

    @property
    def level(self) -> float:
        """Latest RMS level for the waveform overlay (0..~1)."""
        return self._latest_level

    @property
    def recording(self) -> bool:
        return self._recording
