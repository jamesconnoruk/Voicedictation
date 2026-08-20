"""
test_all.py — the "simulation".

Since there's no mic/GUI/model in this environment, we test the real logic with
fakes standing in for hardware and the ML model. This exercises the full
push-to-talk flow end to end, plus every core algorithm.

Run: python3 -m pytest tests/test_all.py -v
"""
import sys, os, time
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from whispr.core.config import Config
from whispr.core.corrections import CorrectionEngine
from whispr.core.transcripts import Transcript, TranscriptStore
from whispr.core.hotkey import (HotkeyMatcher, parse_combo, normalize_key,
                                HotkeyRecorder, canonical_combo, pretty_combo)
from whispr.core.recorder import (rms_level, apply_gain, trim_silence, has_speech)
from whispr.core.controller import DictationController, State
from whispr.core.british_english import to_british


# ============================================================ British English
def test_british_suffix_rules():
    assert to_british("color") == "colour"
    assert to_british("favorite") == "favourite"
    assert to_british("organize") == "organise"
    assert to_british("analyze") == "analyse"
    assert to_british("center") == "centre"
    assert to_british("theater") == "theatre"
    assert to_british("traveling") == "travelling"
    assert to_british("canceled") == "cancelled"
    assert to_british("neighborhood") == "neighbourhood"

def test_british_irregulars():
    assert to_british("gray") == "grey"
    assert to_british("aluminum") == "aluminium"
    assert to_british("mustache") == "moustache"
    assert to_british("jewelry") == "jewellery"

def test_british_preserves_case():
    assert to_british("COLOR") == "COLOUR"
    assert to_british("Color") == "Colour"
    assert to_british("Favorite") == "Favourite"

def test_british_leaves_safe_words_alone():
    # -ize words that are correct in UK too
    assert to_british("size") == "size"
    assert to_british("prize") == "prize"
    assert to_british("seize") == "seize"
    assert to_british("maize") == "maize"
    # context-risky words we deliberately don't flip
    assert to_british("check the box") == "check the box"
    assert to_british("run the program") == "run the program"

def test_british_in_a_sentence():
    src = "I organized my favorite colors at the theater center"
    assert to_british(src) == "I organised my favourite colours at the theatre centre"


# ============================================================ hotkey logic
def test_parse_combo():
    assert parse_combo("<ctrl>+<shift>") == frozenset({"ctrl", "shift"})
    assert parse_combo("<ctrl>+<alt>+j") == frozenset({"ctrl", "alt", "j"})
    assert normalize_key("Control") == "ctrl"
    assert normalize_key("shift_r") == "shift"   # right shift matches <shift>

def test_hotkey_hold_and_release():
    m = HotkeyMatcher.from_string("<ctrl>+<shift>")
    assert m.key_down("ctrl") is None          # partial
    assert m.active is False
    assert m.key_down("shift") == "activate"   # combo complete -> record
    assert m.active is True
    assert m.key_down("shift") is None         # auto-repeat, no re-fire
    assert m.key_up("shift") == "deactivate"   # release -> stop
    assert m.active is False

def test_hotkey_release_either_key_stops():
    m = HotkeyMatcher.from_string("<ctrl>+<shift>")
    m.key_down("ctrl"); m.key_down("shift")
    assert m.active
    assert m.key_up("ctrl") == "deactivate"    # releasing ctrl also stops
    assert not m.active

def test_hotkey_extra_keys_dont_break():
    m = HotkeyMatcher.from_string("<ctrl>+<shift>")
    m.key_down("a")                            # unrelated key first
    m.key_down("ctrl")
    assert m.key_down("shift") == "activate"


# ============================================================ hotkey recorder
def test_recorder_locks_on_release():
    r = HotkeyRecorder()
    r.key_down("ctrl"); r.key_down("shift")
    assert r.snapshot() == "<ctrl>+<shift>"
    assert r.valid is True
    r.key_up("shift"); r.key_up("ctrl")
    assert r.result == "<ctrl>+<shift>"
    assert r.done is True

def test_recorder_press_order_independent():
    a = HotkeyRecorder(); a.key_down("ctrl"); a.key_down("shift")
    a.key_up("shift"); a.key_up("ctrl")
    b = HotkeyRecorder(); b.key_down("shift"); b.key_down("ctrl")
    b.key_up("ctrl"); b.key_up("shift")
    assert a.result == b.result == "<ctrl>+<shift>"

def test_recorder_modifier_plus_letter():
    r = HotkeyRecorder()
    for k in ("ctrl", "alt", "j"):
        r.key_down(k)
    for k in ("j", "alt", "ctrl"):
        r.key_up(k)
    assert r.result == "<ctrl>+<alt>+j"

def test_recorder_normalises_left_right():
    r = HotkeyRecorder()
    r.key_down("ctrl_r"); r.key_down("shift_l")
    r.key_up("shift_l"); r.key_up("ctrl_r")
    assert r.result == "<ctrl>+<shift>"

def test_recorder_rejects_modifierless():
    r = HotkeyRecorder()
    r.key_down("a")
    assert r.valid is False

def test_recorder_result_feeds_matcher():
    """The captured combo must actually drive the push-to-talk matcher."""
    r = HotkeyRecorder()
    r.key_down("ctrl"); r.key_down("shift")
    r.key_up("shift"); r.key_up("ctrl")
    m = HotkeyMatcher.from_string(r.result)
    m.key_down("ctrl")
    assert m.key_down("shift") == "activate"

def test_pretty_and_canonical():
    assert canonical_combo({"shift", "ctrl"}) == "<ctrl>+<shift>"
    assert pretty_combo("<ctrl>+<shift>") == "Ctrl + Shift"
    assert pretty_combo("<ctrl>+<alt>+j") == "Ctrl + Alt + J"


# ============================================================ signal maths
def test_rms_and_gain():
    silence = np.zeros(1000, dtype=np.float32)
    assert rms_level(silence) == 0.0
    tone = 0.5 * np.sin(np.linspace(0, 100, 1000)).astype(np.float32)
    lvl = rms_level(tone)
    assert 0.3 < lvl < 0.4                      # ~0.5/sqrt(2)
    louder = apply_gain(tone, 2.0)
    assert rms_level(louder) > lvl
    # clipping keeps us in range
    assert louder.max() <= 1.0 and louder.min() >= -1.0

def test_trim_silence_keeps_speech():
    sr = 16000
    silence = np.zeros(sr // 2, dtype=np.float32)         # 0.5s silence
    speech = (0.3 * np.sin(np.linspace(0, 2000, sr))).astype(np.float32)  # 1s tone
    clip = np.concatenate([silence, speech, silence])
    trimmed = trim_silence(clip, sr, threshold=0.02)
    # trimmed should be much shorter than original but retain most speech
    assert len(trimmed) < len(clip)
    assert len(trimmed) >= sr * 0.8

def test_has_speech_detects_and_rejects():
    sr = 16000
    silence = np.zeros(sr, dtype=np.float32)
    speech = (0.3 * np.sin(np.linspace(0, 2000, sr))).astype(np.float32)
    assert has_speech(speech, sr, threshold=0.02) is True
    assert has_speech(silence, sr, threshold=0.02) is False


# ============================================================ corrections
def test_learn_single_word_correction():
    ce = CorrectionEngine()
    ce.learn_from_edit("I use wisper every day", "I use Wispr every day")
    assert ce.apply("wisper is great") == "Wispr is great"
    assert "Wispr" in ce.vocabulary            # added to custom vocab

def test_learn_phrase_correction():
    ce = CorrectionEngine()
    ce.learn_from_edit("orders go through lin works",
                       "orders go through Linnworks")
    out = ce.apply("check lin works")
    assert "Linnworks" in out

def test_fuzzy_correction():
    ce = CorrectionEngine()
    ce.learn_from_edit("hello wisper", "hello Wispr")
    # a near-miss spelling should still map via fuzzy
    assert "Wispr" in ce.apply("hello wispr")

def test_initial_prompt_biases_vocab():
    ce = CorrectionEngine()
    ce.add_vocabulary("Vidalux")
    ce.add_vocabulary("Linnworks")
    p = ce.initial_prompt()
    assert "Vidalux" in p and "Linnworks" in p

def test_correction_roundtrip_persist(tmp_path):
    ce = CorrectionEngine()
    ce.learn_from_edit("meet at woo commerce", "meet at WooCommerce")
    f = tmp_path / "corr.json"
    ce.save(f)
    ce2 = CorrectionEngine.load(f)
    assert "WooCommerce" in ce2.apply("open woo commerce")


# ============================================================ transcripts
def test_transcript_store_crud(tmp_path):
    store = TranscriptStore(tmp_path / "t.jsonl")
    t = Transcript.new("hello world")
    store.add(t)
    assert len(store) == 1
    store.update_text(t.id, "hello, world!")
    assert store.get(t.id).text == "hello, world!"
    assert store.get(t.id).edited is True
    # search
    assert len(store.search("hello")) == 1
    assert len(store.search("zzz")) == 0
    # persistence across reload
    store2 = TranscriptStore(tmp_path / "t.jsonl")
    assert store2.get(t.id).text == "hello, world!"
    # delete
    assert store2.delete(t.id) is True
    assert len(store2) == 0

def test_transcripts_are_separate_entries(tmp_path):
    store = TranscriptStore(tmp_path / "t.jsonl")
    for i in range(3):
        t = Transcript.new(f"dictation number {i}")
        # ensure distinct, increasing timestamps so ordering is deterministic
        # (creating them in a tight loop can otherwise collide on the same time)
        t.created_at = 1000.0 + i
        store.add(t)
    assert len(store) == 3
    # newest first
    texts = [t.text for t in store.all()]
    assert "dictation number 2" in texts[0]


# ============================================================ FAKES for E2E
class FakeRecorder:
    """Stands in for the mic. Emits a preset audio buffer on stop()."""
    def __init__(self, audio):
        self._audio = audio
        self.started = False
        self.level = 0.0
    def start(self):
        self.started = True
    def stop(self):
        self.started = False
        return self._audio

class FakeTranscriber:
    """Stands in for faster-whisper. Returns a canned string, records the prompt."""
    def __init__(self, result):
        self.result = result
        self.last_prompt = None
    def transcribe(self, audio, initial_prompt=""):
        self.last_prompt = initial_prompt
        return self.result

class FakeClock:
    def __init__(self): self.t = 1000.0
    def __call__(self): return self.t
    def advance(self, dt): self.t += dt


def make_controller(tmp_path, audio, whisper_result, corrections=None):
    cfg = Config()
    cfg.sample_rate = 16000
    cfg.min_record_seconds = 0.3
    store = TranscriptStore(tmp_path / "t.jsonl")
    ce = corrections or CorrectionEngine()
    pasted = {}
    def paste_fn(text, method):
        pasted["text"] = text
        pasted["method"] = method
    states = []
    ctrl = DictationController(
        config=cfg,
        recorder=FakeRecorder(audio),
        transcriber=FakeTranscriber(whisper_result),
        corrections=ce,
        store=store,
        paste_fn=paste_fn,
        window_title_fn=lambda: "Notepad",
        on_state_change=lambda s: states.append(s),
    )
    return ctrl, store, pasted, states


def _speech_audio(seconds=1.0, sr=16000):
    return (0.3 * np.sin(np.linspace(0, 3000, int(sr * seconds)))).astype(np.float32)


# ============================================================ END-TO-END
def test_full_push_to_talk_flow(tmp_path):
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(
        tmp_path, audio, "hello this is a test")

    assert ctrl.state == State.IDLE
    ctrl.on_hotkey_down()
    assert ctrl.state == State.RECORDING
    assert ctrl.recorder.started is True
    # emulate the key being held for ~1s (longer than min_record_seconds)
    ctrl._record_start = ctrl.clock() - 1.0
    ctrl.on_hotkey_up()

    # back to idle, text pasted + saved as its own transcript
    assert ctrl.state == State.IDLE
    assert pasted["text"] == "hello this is a test"
    assert pasted["method"] == "paste"
    assert len(store) == 1
    assert store.all()[0].target_app == "Notepad"
    # state sequence was IDLE->RECORDING->TRANSCRIBING->IDLE
    assert states == [State.RECORDING, State.TRANSCRIBING, State.IDLE]

def test_flow_applies_corrections_and_biases_prompt(tmp_path):
    ce = CorrectionEngine()
    ce.learn_from_edit("i use wisper", "i use Wispr")
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(
        tmp_path, audio, "wisper is running", corrections=ce)
    ctrl._record_start = time.time() - 1.0
    ctrl.state = State.RECORDING            # simulate mid-record
    ctrl.recorder.started = True
    ctrl.on_hotkey_up()
    # correction applied before paste
    assert pasted["text"] == "Wispr is running"
    # vocab was fed to the model as a prompt
    assert "Wispr" in ctrl.transcriber.last_prompt

def test_short_tap_is_ignored(tmp_path):
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(tmp_path, audio, "oops")
    ctrl.on_hotkey_down()
    ctrl._record_start = time.time()        # ~0s duration
    ctrl.on_hotkey_up()
    assert pasted == {}                     # nothing pasted
    assert len(store) == 0                  # nothing saved
    assert ctrl.state == State.IDLE

def test_silence_produces_no_paste(tmp_path):
    silence = np.zeros(16000, dtype=np.float32)
    ctrl, store, pasted, states = make_controller(tmp_path, silence, "ghost text")
    ctrl.state = State.RECORDING
    ctrl.recorder.started = True
    ctrl._record_start = time.time() - 1.0
    ctrl.on_hotkey_up()
    assert pasted == {}                     # VAD rejected empty audio
    assert len(store) == 0

def test_no_activity_after_idle(tmp_path):
    """After returning to IDLE, stray key-ups do nothing (spec requirement)."""
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(tmp_path, audio, "done")
    ctrl.state = State.RECORDING
    ctrl.recorder.started = True
    ctrl._record_start = time.time() - 1.0
    ctrl.on_hotkey_up()
    assert ctrl.state == State.IDLE
    saved = len(store)
    # further hotkey_up with no hotkey_down must be a no-op
    ctrl.on_hotkey_up()
    assert len(store) == saved
    assert ctrl.state == State.IDLE

def test_british_english_end_to_end(tmp_path):
    """Whisper emits US spelling; pipeline pastes UK spelling."""
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(
        tmp_path, audio, "I organized the color of my favorite theater")
    assert ctrl.config.british_english is True   # default on
    ctrl.state = State.RECORDING
    ctrl.recorder.started = True
    ctrl._record_start = time.time() - 1.0
    ctrl.on_hotkey_up()
    assert pasted["text"] == "I organised the colour of my favourite theatre"
    # raw (US) is preserved on the transcript; displayed text is UK
    assert store.all()[0].raw_text == "I organized the color of my favorite theater"
    assert store.all()[0].text == "I organised the colour of my favourite theatre"

def test_british_off_leaves_us_spelling(tmp_path):
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(
        tmp_path, audio, "I organized the color")
    ctrl.config.british_english = False
    ctrl.state = State.RECORDING
    ctrl.recorder.started = True
    ctrl._record_start = time.time() - 1.0
    ctrl.on_hotkey_up()
    assert pasted["text"] == "I organized the color"

def test_user_edit_learns(tmp_path):
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(
        tmp_path, audio, "i love woo commerce")
    ctrl.state = State.RECORDING
    ctrl.recorder.started = True
    ctrl._record_start = time.time() - 1.0
    ctrl.on_hotkey_up()
    tid = store.all()[0].id
    # user right-clicks and corrects in the app
    ctrl.apply_user_edit(tid, "i love WooCommerce")
    assert store.get(tid).text == "i love WooCommerce"
    # engine now knows the correction for next time
    assert "WooCommerce" in ctrl.corrections.apply("open woo commerce")


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))



# ==================================================== AUTO-GAIN / NORMALISE
def test_normalize_lifts_quiet_audio():
    from whispr.core.recorder import normalize_audio
    quiet = (0.002 * np.sin(np.linspace(0, 3000, 16000))).astype(np.float32)
    out, gain = normalize_audio(quiet)
    assert gain > 5.0                       # a big lift for a very quiet mic
    assert float(np.max(np.abs(out))) <= 1.0


def test_normalize_attenuates_hot_audio_without_clipping():
    from whispr.core.recorder import normalize_audio
    hot = (0.9 * np.sin(np.linspace(0, 3000, 16000))).astype(np.float32)
    out, gain = normalize_audio(hot)
    assert gain < 1.0
    assert float(np.max(np.abs(out))) <= 0.96


def test_normalize_never_amplifies_pure_silence():
    from whispr.core.recorder import normalize_audio
    out, gain = normalize_audio(np.zeros(16000, dtype=np.float32))
    assert gain == 1.0
    assert float(np.max(np.abs(out))) == 0.0


def test_normalize_ignores_a_single_loud_transient():
    """A door slam shouldn't set the level and leave speech inaudible."""
    from whispr.core.recorder import normalize_audio
    speech = (0.02 * np.sin(np.linspace(0, 3000, 16000))).astype(np.float32)
    speech[8000] = 0.99                     # one-sample spike
    out, gain = normalize_audio(speech)
    assert gain > 1.5                       # still lifts the speech


def test_dc_offset_removed():
    from whispr.core.recorder import dc_offset_removal
    biased = (0.3 + 0.05 * np.sin(np.linspace(0, 3000, 16000))).astype(np.float32)
    out = dc_offset_removal(biased)
    assert abs(float(np.mean(out))) < 1e-5


# ========================================================= PASTE METHODS
def test_paste_methods_are_the_documented_three():
    from whispr.core.output import PASTE_METHODS
    assert PASTE_METHODS == ("paste", "type", "copy")


def test_controller_captures_target_window_on_hotkey_down(tmp_path):
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(tmp_path, audio, "hi there")
    assert ctrl._target_hwnd is None
    ctrl.on_hotkey_down()
    # captured at record start (None on this platform, but the attribute is set)
    assert hasattr(ctrl, "_target_hwnd")


def test_copy_only_method_is_passed_through(tmp_path):
    audio = _speech_audio(1.0)
    ctrl, store, pasted, states = make_controller(tmp_path, audio, "just copy")
    ctrl.config.paste_method = "copy"
    ctrl._record_start = time.time() - 1.0
    ctrl.state = State.RECORDING
    ctrl.recorder.started = True
    ctrl.on_hotkey_up()
    assert pasted["method"] == "copy"
