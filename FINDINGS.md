# Diagnosis from voxkey_doctor_log.txt (20 Aug 2026)

Three independent faults. Any one of them alone stops dictation dead.

## 1. VoxKey was recording from a silent device  [CONFIRMED]

    config.json:  input_device_index = None   (= system default)
    system default = index 1, "Broadcast Stream Mix (4- TC-HELICON GoXLR)"
    measured RMS  = 0.00001   -> pure digital silence

Your GoXLR sets its Broadcast Stream Mix as the Windows default input. It
opens perfectly and returns nothing. VoxKey therefore recorded silence on
every single dictation, VAD correctly rejected it, and nothing was pasted.

Wispr sidesteps this by using an explicitly chosen device: HDMI (Cam Link 4K).

15 devices DO deliver audio. Best options:
    index 45  HDMI (Cam Link 4K)              WASAPI 48 kHz  RMS 0.049  <- matches Wispr
    index 44  Chat Mic (4- TC-HELICON GoXLR)  WASAPI 48 kHz  RMS 0.059  <- loudest
    index 43  Sample (4- TC-HELICON GoXLR)    WASAPI 48 kHz  RMS 0.058

## 2. The configured hotkey was not the one being pressed  [CONFIRMED]

    config.json: hotkey = "<ctrl>+<shift>"
    key trace:   DOWN cmd -> DOWN ctrl_l   (i.e. Ctrl + Win)
    Wispr:       "Hold Ctrl + Win and speak"

You've been holding Wispr's combo. VoxKey's matcher never saw its own combo,
so on/off never fired. Note the two apps must NOT share a hotkey — set VoxKey
to something else, or quit Wispr while testing.

## 3. The speech worker crashes while loading the model  [ROOT CAUSE UNKNOWN]

    starting whisper worker: VoxKey-Whisper.exe small en
    [whisper-worker] loading model small
    worker failed ... restarting and retrying once
    ERROR  the speech engine stopped unexpectedly

Dies ~13 s into loading, twice, with no Python traceback. That means a NATIVE
crash inside ctranslate2. It is not a download problem: the model cache is
486 MB, so `small` is fully present.

Run whisper_probe.py to separate the two possible causes:
  A) loads fine in plain Python  -> PyInstaller packaging is at fault
  B) crashes in plain Python too -> ctranslate2 can't run this config on this CPU

## Also found (a bug in my own code, exposed by your log)

    [FAIL] native clipboard — OverflowError: int too long to convert

output.py called SetClipboardData without declaring ctypes argtypes, so the
64-bit clipboard HANDLE was truncated. The native path never worked on 64-bit
Windows; it was silently falling through to pyperclip. Fixed.

## Not a fault (ignore it)

    [FAIL] keys still marked DOWN at end — ['cmd']

That's just the listener stopping while you happened to be holding Win.
Section [5] read the real keyboard state a moment later and found it clean.

# Changes in this build

- recorder.py: devices resolved by NAME first (indices shuffle when USB gear is
  re-plugged), and known silent virtual endpoints — Stream Mix, Stereo Mix,
  Sound Mapper, Primary Sound Capture, loopback — are never auto-selected.
  Also never forces 16 kHz on the device; always opens at the native rate.
- config.py / voice_setup.py: the chosen microphone's NAME is saved alongside
  its index.
- transcribe_worker.py: faulthandler armed (writes worker_fault.log on a native
  crash), and a fallback chain small/int8 -> small/float32 -> base -> tiny, so
  you get working dictation even if one configuration is unusable here.
- transcriber.py: decodes the worker's Windows exit code (access violation vs
  missing DLL vs bad image), dumps the native crash traceback into the log, and
  no longer sets the working directory to read-only Program Files.
- output.py: correct ctypes argtypes for the Win32 clipboard.

# 20 Aug — autofix log: ROOT CAUSE FOUND

Every configuration crashed identically:

    small/int8     0xC0000005 ACCESS VIOLATION
    small/float32  0xC0000005
    base/int8      0xC0000005
    base/float32   0xC0000005
    tiny/int8      0xC0000005
    tiny/float32   0xC0000005

tiny/float32 is the least demanding configuration that exists. Its failing
identically rules out the model size, the compute type, and the CPU — if the
CPU lacked an instruction set, float32 would have worked.

The fault is in VoxKey.spec: ONE COLLECT merged the app bundle and the worker
bundle into a single folder. PyQt6 and ctranslate2 both ship their own copies
of msvcp140.dll, vcruntime140.dll, zlib, libcrypto and the OpenMP runtime.
Merging collapses duplicates to one copy, so VoxKey-Whisper.exe was loading
Qt's build of libraries it was never linked against, and died during
ctranslate2's native init every time.

FIX: two separate bundles.
  dist/VoxKey/           app + watchdog (Qt)
  dist/VoxKey-Whisper/   speech engine (ctranslate2), own DLLs, no Qt at all
CI nests the second inside dist/VoxKey/whisper/ so the installer ships one
tree, and smoke-tests the worker so this regression can't ship again silently.
The worker's Analysis now EXCLUDES PyQt6/PySide/sounddevice/pynput outright.

Also added: config.engine_python, and automatic fallback to a system Python
that has faster-whisper if the bundled worker exits with a native-crash code.

# 20 Aug — speed and accuracy

## Why it was slow (my fault)

    cpu_threads=1     one core doing all the work
    beam_size=5       and searching 5 candidate transcriptions at once

Both were crash mitigations from when 0xC0000005 was misdiagnosed as a
threading race. The real cause was the DLL collision, so the throttle bought
nothing and cost a great deal. Measured earlier on this machine: 10.0s of
inference for a 2-second clip — RTF ~5, i.e. five seconds of compute per
second of speech.

Changes:
  * cpu_threads now auto = cores-1 (capped 8). Roughly linear speed-up.
  * beam_size now 1 (greedy). 2-3x faster; beam search mainly helps long
    ambiguous audio, not short dictation.
  * temperature fallback [0.0, 0.2, 0.4] instead of always 0.0 — only retries
    when the greedy output looks degenerate.
  * without_timestamps=True — less work, fewer hallucinated trailing words.

## Why recognition was poor

1. DOUBLE VAD. The controller ran an energy-threshold VAD and trimmed the
   audio, then the worker ran Silero VAD again. The energy threshold came from
   calibration (0.0162 here) and sat above quiet word onsets, so the start of
   sentences was cut off before Whisper ever saw it. The controller now only
   does a cheap "was anything said" check at a fixed low floor and passes the
   audio through untrimmed; Silero does the real boundary detection.

2. MULTILINGUAL MODEL ON ENGLISH. `small` spends capacity on 98 languages.
   `small.en` / `distil-small.en` are English-only: more accurate AND faster.
   Default is now distil-small.en, and the model list is ordered fastest to
   most accurate with the .en variants first.

3. VAD parameters: speech_pad_ms=400 and threshold=0.35 so quiet onsets
   aren't clipped; min_silence_duration_ms=500 so natural pauses mid-sentence
   don't split the utterance.

## voxkey_tune.py

Records a sample of his actual voice, then benchmarks model/thread/beam
combinations against it and writes the winner to config.json. Reports RTF and
a word-match score per configuration, including the old settings for
comparison. Picks the fastest configuration within 3% of the best accuracy.

# 20 Aug — amplification, speed, paste

## The tune log was invalid (bug in my tuner)

  microphone: system default (index None)
  captured 8.0s of audio, RMS 0.0000

It recorded silence, every model returned empty text, all scored 0%, and it
still picked a "winner" from a six-way tie of nothing and wrote it to config.
The RTF numbers only measure how fast each model processes silence.

Cause: input_device_index was back to None. The OLD installed build resets it
when the saved index doesn't match a current device, and index 21 had shifted.

Tuner now: resolves the device by NAME (falling back to any non-loopback
input), warns if the selected device is a known-silent endpoint, ABORTS if the
recording is silent (RMS < 0.0015), and refuses to write settings if every
configuration scores under 25%.

## Amplification — auto-gain instead of a fixed multiplier

normalize_audio() measures the level of the SPOKEN parts (90th-percentile gate,
so silence doesn't drag it down), scales toward target RMS 0.08, and limits
using the 99.9th percentile rather than the absolute peak — so a door slam or
keyboard clack can't cap the gain and leave speech inaudible. DC offset removed
first. Handles a 150x range of input levels; verified by unit tests.

## Why nothing pasted on release

The overlay could take focus, so the simulated Ctrl+V landed on IT, not the
document. Two fixes:
  * overlay gets WindowDoesNotAcceptFocus + WS_EX_NOACTIVATE
  * the controller captures the foreground window at hotkey-DOWN and the paste
    path restores focus to it first, using AttachThreadInput (Windows blocks
    SetForegroundWindow from a non-foreground process otherwise)

## Paste method is now an explicit choice

  paste  — copy, then press Ctrl+V for you   (default, the Wispr behaviour)
  type   — simulate the keystrokes
  copy   — copy only; you press Ctrl+V

Settings labels it "When I finish speaking". "clipboard" still maps to "paste".
