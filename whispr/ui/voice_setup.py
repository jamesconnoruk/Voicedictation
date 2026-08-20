"""
voice_setup.py — the "Voice Trainer".

Honest framing (shown to the user in the UI too): local Whisper is already
speaker-independent, so we don't retrain the neural net. Instead this does the
two things that genuinely improve YOUR accuracy:

  1. CALIBRATION — you read a few sentences. We measure your mic's noise floor
     and typical speech level, then set silence_threshold and mic_gain so VAD
     and levels are tuned to your voice + room + mic. This is what stops it
     cutting you off or pasting empty/ghost text.

  2. CUSTOM VOCABULARY — you add names/jargon (Vidalux, Linnworks, WooCommerce…).
     These bias Whisper via initial_prompt AND seed the correction engine, so
     they come out right from the start.

The calibration READS use the real recorder at runtime; here we expose the
widget + the pure calibration maths (testable).
"""
from __future__ import annotations
import numpy as np

CALIBRATION_SENTENCES = [
    "The quick brown fox jumps over the lazy dog.",
    "I would like to dictate my emails quickly and accurately.",
    "Please transcribe everything I say without missing a word.",
    "Send the order to the warehouse before five o'clock today.",
    "This sentence has numbers like one, two, three and forty seven.",
    "My favourite colour is a deep shade of ocean blue.",
    "The meeting is scheduled for next Thursday at half past ten.",
    "She sells seashells by the seashore on sunny summer days.",
    "Could you please forward that report to the whole team?",
    "Peter Piper picked a peck of pickled peppers quickly.",
    "The invoice total came to three hundred and forty two pounds.",
    "Remember to back up the database before running the update.",
]


def compute_calibration(noise_rms: float, speech_rms: float) -> dict:
    """
    Given measured RMS of silence (noise floor) and of speech, derive:
      - silence_threshold: sits between noise floor and speech, closer to noise
      - mic_gain: boost quiet mics so speech RMS lands near a target (~0.12)
    Pure maths -> unit testable.
    """
    noise = max(0.0, float(noise_rms))
    speech = max(noise + 1e-4, float(speech_rms))

    # threshold: 35% of the way from noise to speech (biased toward noise so we
    # don't clip quiet word onsets), with a small floor.
    threshold = noise + 0.35 * (speech - noise)
    threshold = max(0.006, min(threshold, 0.08))

    # gain: bring speech up toward target level, but clamp to a sane range
    target = 0.12
    gain = target / speech if speech > 0 else 1.0
    gain = max(0.5, min(gain, 6.0))

    return {"silence_threshold": round(threshold, 4),
            "mic_gain": round(gain, 3)}


try:
    from PyQt6 import QtCore, QtWidgets
    _HAVE_QT = True
except Exception:  # pragma: no cover
    _HAVE_QT = False


if _HAVE_QT:
    def build_voice_setup_widget(ctx):
        w = QtWidgets.QWidget()
        lay = QtWidgets.QVBoxLayout(w)

        intro = QtWidgets.QLabel(
            "<b>Voice Setup</b><br>"
            "Pick your microphone and test it, then read the sentences to "
            "calibrate, and add any names or jargon it should always get right.")
        intro.setWordWrap(True)
        lay.addWidget(intro)

        # --- microphone picker + live level meter ---
        mic_box = QtWidgets.QGroupBox("1. Microphone")
        mic_lay = QtWidgets.QVBoxLayout(mic_box)

        from ..core.recorder import list_input_devices, default_input_index

        mic_row = QtWidgets.QHBoxLayout()
        mic_combo = QtWidgets.QComboBox()
        devices = list_input_devices()
        mic_combo.addItem("System default", None)
        for idx, name in devices:
            mic_combo.addItem(name, idx)
        # preselect the configured device
        cur = getattr(ctx.config, "input_device_index", None)
        for i in range(mic_combo.count()):
            if mic_combo.itemData(i) == cur:
                mic_combo.setCurrentIndex(i)
                break
        mic_row.addWidget(mic_combo, 1)
        test_btn = QtWidgets.QPushButton("Test")
        test_btn.setCheckable(True)
        mic_row.addWidget(test_btn)
        mic_lay.addLayout(mic_row)

        # live level bar
        level_bar = QtWidgets.QProgressBar()
        level_bar.setRange(0, 100)
        level_bar.setValue(0)
        level_bar.setTextVisible(False)
        level_bar.setFixedHeight(16)
        level_bar.setStyleSheet("""
            QProgressBar { background:#141416; border:1px solid #2a2a30;
                border-radius:8px; }
            QProgressBar::chunk { background:#4ade80; border-radius:7px; }
        """)
        mic_lay.addWidget(level_bar)
        level_hint = QtWidgets.QLabel("Click Test and speak — the bar should move.")
        level_hint.setStyleSheet("color:#8a8a92; font-size:11px;")
        mic_lay.addWidget(level_hint)
        lay.addWidget(mic_box)

        # wire the picker to save the choice + reconfigure the recorder
        def on_mic_changed():
            ctx.config.input_device_index = mic_combo.currentData()
            # Store the NAME too — indices shuffle when USB devices are
            # re-plugged, and a stale index silently selects a different mic.
            label = mic_combo.currentText()
            ctx.config.input_device_name = (
                None if mic_combo.currentData() is None
                else label.split("  [")[0].strip())
            ctx.config.save()
            try:
                ctx.recorder.device_index = ctx.config.input_device_index
                ctx.recorder.device_name = ctx.config.input_device_name
            except Exception:
                pass
        mic_combo.currentIndexChanged.connect(on_mic_changed)

        # live meter: a short-lived test recorder + timer polling its level
        test_state = {"rec": None, "timer": None}

        def toggle_test():
            if test_btn.isChecked():
                # start a test capture on the selected device
                try:
                    from ..core.recorder import Recorder
                    rec = Recorder(device_index=mic_combo.currentData(),
                                   gain=getattr(ctx.config, "mic_gain", 1.0))
                    rec.start()
                    test_state["rec"] = rec
                    timer = QtCore.QTimer()
                    def poll():
                        r = test_state.get("rec")
                        if r is None:
                            return
                        lvl = min(1.0, r.level * 6.5)
                        level_bar.setValue(int(lvl * 100))
                    timer.timeout.connect(poll)
                    timer.start(40)
                    test_state["timer"] = timer
                    test_btn.setText("Stop")
                    level_hint.setText("Listening… speak now.")
                except Exception as e:
                    test_btn.setChecked(False)
                    level_hint.setText(f"Couldn't open that mic: {e}")
            else:
                # stop
                t = test_state.get("timer")
                if t:
                    t.stop()
                r = test_state.get("rec")
                if r:
                    try:
                        r.stop()
                    except Exception:
                        pass
                test_state["rec"] = None
                test_state["timer"] = None
                level_bar.setValue(0)
                test_btn.setText("Test")
                level_hint.setText("Click Test and speak — the bar should move.")
        test_btn.clicked.connect(toggle_test)

        # --- calibration ---
        cal_box = QtWidgets.QGroupBox("2. Calibrate to your voice")
        cal_lay = QtWidgets.QVBoxLayout(cal_box)
        sentence_lbl = QtWidgets.QLabel(CALIBRATION_SENTENCES[0])
        sentence_lbl.setStyleSheet("font-size:15px; padding:8px; color:#f0f0f5;")
        sentence_lbl.setWordWrap(True)
        cal_lay.addWidget(sentence_lbl)

        status = QtWidgets.QLabel("Press ‘Start reading’ and read the sentence aloud.")
        status.setStyleSheet("color:#8a8a94;")
        cal_lay.addWidget(status)

        btn = QtWidgets.QPushButton("Start reading")
        cal_lay.addWidget(btn)
        lay.addWidget(cal_box)

        state = {"idx": 0, "noise": [], "speech": []}

        def do_step():
            # ctx.calibrate_once() records ~2s, returns (noise_rms, speech_rms)
            try:
                noise, speech = ctx.calibrate_once(CALIBRATION_SENTENCES[state["idx"]])
            except Exception as ex:
                status.setText(f"Mic error: {ex}")
                return
            state["noise"].append(noise)
            state["speech"].append(speech)
            state["idx"] += 1
            if state["idx"] < len(CALIBRATION_SENTENCES):
                sentence_lbl.setText(CALIBRATION_SENTENCES[state["idx"]])
                status.setText(
                    f"Captured {state['idx']}/{len(CALIBRATION_SENTENCES)}. "
                    "Read the next one.")
            else:
                noise = float(np.median(state["noise"]))
                speech = float(np.median(state["speech"]))
                result = compute_calibration(noise, speech)
                ctx.config.silence_threshold = result["silence_threshold"]
                ctx.config.mic_gain = result["mic_gain"]
                ctx.config.save()
                ctx.on_settings_changed()
                status.setText(
                    f"✓ Calibrated. Silence threshold {result['silence_threshold']}, "
                    f"mic gain {result['mic_gain']}×. You're set.")
                btn.setText("Re-calibrate")
                state.update({"idx": 0, "noise": [], "speech": []})
                sentence_lbl.setText(CALIBRATION_SENTENCES[0])

        btn.clicked.connect(do_step)

        # --- vocabulary ---
        vocab_box = QtWidgets.QGroupBox("3. Custom words (names, jargon, brands)")
        vlay = QtWidgets.QVBoxLayout(vocab_box)
        vlist = QtWidgets.QListWidget()
        for word in ctx.corrections.vocabulary:
            vlist.addItem(word)
        add_row = QtWidgets.QHBoxLayout()
        add_edit = QtWidgets.QLineEdit()
        add_edit.setPlaceholderText("e.g. Vidalux, Linnworks, WooCommerce")
        add_btn = QtWidgets.QPushButton("Add")
        add_row.addWidget(add_edit); add_row.addWidget(add_btn)

        def add_word():
            word = add_edit.text().strip()
            if word:
                ctx.corrections.add_vocabulary(word)
                ctx.save_corrections()
                vlist.addItem(word)
                add_edit.clear()
        add_btn.clicked.connect(add_word)
        add_edit.returnPressed.connect(add_word)

        vlay.addWidget(vlist)
        vlay.addLayout(add_row)
        lay.addWidget(vocab_box)
        lay.addStretch(1)
        return w
