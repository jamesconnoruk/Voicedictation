"""
controller.py — the brain. A push-to-talk state machine wiring together
recorder, transcriber, correction engine, transcript store and output.

Designed so the STATE LOGIC is testable with fakes: we inject the recorder,
transcriber and output function. The real app passes real ones; tests pass
fakes and assert the sequence of states and side effects.

States:
    IDLE        -> hotkey down -> RECORDING (overlay shows, mic starts)
    RECORDING   -> hotkey up   -> TRANSCRIBING (overlay hides, mic stops)
    TRANSCRIBING-> done         -> IDLE (text corrected, pasted, saved)

Crucially: once we return to IDLE there is NO further activity until the next
hotkey press or the user opening the app — exactly as specified.
"""
from __future__ import annotations
import time
import threading
from enum import Enum
from typing import Callable

import numpy as np

from .transcripts import Transcript, TranscriptStore
from .corrections import CorrectionEngine
from .recorder import (trim_silence, has_speech, rms_level,
                       normalize_audio, dc_offset_removal)
from .british_english import to_british, UK_ACCENT_PROMPT


class State(Enum):
    IDLE = "idle"
    RECORDING = "recording"
    TRANSCRIBING = "transcribing"


class DictationController:
    def __init__(self, *, config, recorder, transcriber,
                 corrections: CorrectionEngine, store: TranscriptStore,
                 paste_fn: Callable[[str, str], None],
                 window_title_fn: Callable[[], str] = lambda: "",
                 on_state_change: Callable[["State"], None] = lambda s: None,
                 on_status: Callable[[str], None] = lambda m: None,
                 clock: Callable[[], float] = time.time):
        self.config = config
        self.recorder = recorder
        self.transcriber = transcriber
        self.corrections = corrections
        self.store = store
        self.paste_fn = paste_fn
        self.window_title_fn = window_title_fn
        self.on_state_change = on_state_change
        self.on_status = on_status
        self.clock = clock

        self.state = State.IDLE
        self._record_start = 0.0
        self._target_hwnd = None
        self._lock = threading.Lock()
        self.last_transcript: Transcript | None = None

    def _status(self, msg: str):
        """Tell the user why nothing was pasted, instead of failing silently."""
        try:
            self.on_status(msg)
        except Exception:
            pass

    def _set_state(self, s: State):
        self.state = s
        try:
            self.on_state_change(s)
        except Exception:
            pass

    # ---------------------------------------------------- hotkey edges
    def on_hotkey_down(self):
        with self._lock:
            if self.state is not State.IDLE:
                return
            self._record_start = self.clock()
            self._set_state(State.RECORDING)
        # Remember where the cursor was BEFORE the overlay appears, so the
        # text goes back into that window rather than wherever focus drifted.
        try:
            from .output import foreground_hwnd
            self._target_hwnd = foreground_hwnd()
        except Exception:
            self._target_hwnd = None
        self.recorder.start()

    def on_hotkey_up(self):
        with self._lock:
            if self.state is not State.RECORDING:
                return
            self._set_state(State.TRANSCRIBING)
        audio = self.recorder.stop()
        duration = self.clock() - self._record_start
        # Do the heavy work off the hotkey thread in the real app; inline here
        # keeps tests deterministic. The real UI calls this in a worker.
        self._process(audio, duration)

    # ---------------------------------------------------- core processing
    def _process(self, audio: np.ndarray, duration: float):
        try:
            # Ignore accidental taps
            if duration < self.config.min_record_seconds:
                return
            if audio is None or len(audio) == 0:
                self._status("No audio was captured — check your microphone "
                             "in Voice Setup.")
                return

            sr = self.config.sample_rate
            level = rms_level(audio)

            # ONE gate, not two. We used to run an energy VAD here AND
            # Whisper's Silero VAD in the worker; the energy pass used a
            # calibrated threshold that was often above quiet word onsets, so
            # it chopped the start of sentences before Whisper ever saw them.
            #
            # Now this is only a cheap "was anything said at all" guard using
            # a fixed low floor. Actual speech-boundary detection is left to
            # Silero in the worker, which is far better at it. We do NOT trim
            # the audio — Whisper handles leading/trailing silence fine.
            if level < 0.0015 and not has_speech(audio, sr, 0.004):
                self._status("No speech detected — try speaking a little "
                             "louder, or re-run Voice Setup.")
                return

            # Clean up and normalise before the model sees it. Whisper is
            # trained on speech at a consistent level; feeding it something
            # very quiet costs real accuracy, and a fixed multiplier can't
            # adapt to how close you happen to be sitting.
            if getattr(self.config, "auto_gain", True):
                audio = dc_offset_removal(audio)
                audio, applied = normalize_audio(
                    audio,
                    target_rms=getattr(self.config, "auto_gain_target", 0.08))
                if applied > 1.5 or applied < 0.7:
                    from .applog import log
                    log(f"auto-gain: input rms {level:.5f}, "
                        f"applied {applied:.2f}x")

            # Build the initial prompt: user vocabulary + (optionally) UK accent
            # context, so the decoder is biased toward British spelling.
            prompt = self.corrections.initial_prompt()
            if getattr(self.config, "british_english", False):
                prompt = (UK_ACCENT_PROMPT + " " + prompt).strip()
            raw = self.transcriber.transcribe(audio, initial_prompt=prompt)
            if not raw.strip():
                self._status("Nothing was recognised in that recording.")
                return

            # British spelling normalisation first, then the user's own learned
            # corrections (so user corrections always take precedence).
            corrected = raw
            if getattr(self.config, "british_english", False):
                corrected = to_british(corrected)
            corrected = self.corrections.apply(corrected)

            target = ""
            try:
                target = self.window_title_fn()
            except Exception:
                pass

            try:
                self.paste_fn(corrected, self.config.paste_method,
                              self._target_hwnd)
            except TypeError:
                # older paste_fn signature (tests inject a 2-arg fake)
                self.paste_fn(corrected, self.config.paste_method)

            t = Transcript.new(raw_text=raw, text=corrected,
                               duration_s=duration, target_app=target)
            self.store.add(t)
            self.last_transcript = t
        finally:
            with self._lock:
                self._set_state(State.IDLE)

    # ---------------------------------------------------- correction hook
    def apply_user_edit(self, transcript_id: str, new_text: str):
        """
        Called when the user edits/corrects a saved transcript in the UI.
        Learns from the diff and persists everything.
        """
        t = self.store.get(transcript_id)
        if t is None:
            return None
        original = t.text
        self.store.update_text(transcript_id, new_text)
        # Learn using the text that was actually shown vs the correction
        self.corrections.learn_from_edit(original, new_text)
        return t
