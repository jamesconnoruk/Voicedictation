"""
hotkey.py — global push-to-talk hotkey detection.

Design goal: HOLD to record, RELEASE to stop. That means we can't use
pynput's GlobalHotKeys convenience class (it only fires on press). Instead we
track the live set of pressed keys ourselves and detect the transition:

    - "combo becomes fully held"  -> on_activate()   (start recording)
    - "combo stops being fully held" -> on_deactivate() (stop + transcribe)

The pure matching logic lives in `HotkeyMatcher`, which has NO dependency on
pynput and is fully unit-testable. The `HotkeyListener` is the thin pynput
wrapper used at runtime.
"""
from __future__ import annotations
from dataclasses import dataclass, field


# Canonical modifier/key names we understand in a combo string.
# We normalise left/right variants to the base name so "<ctrl>" matches either.
_ALIASES = {
    "control": "ctrl", "ctrl_l": "ctrl", "ctrl_r": "ctrl",
    "shift_l": "shift", "shift_r": "shift",
    "alt_l": "alt", "alt_r": "alt", "alt_gr": "alt", "option": "alt",
    "cmd": "cmd", "cmd_l": "cmd", "cmd_r": "cmd", "super": "cmd", "win": "cmd",
}


def normalize_key(name: str) -> str:
    """Map a raw key name (from pynput or a config string) to a canonical token."""
    n = name.strip().lower().strip("<>")
    return _ALIASES.get(n, n)


def parse_combo(combo: str) -> frozenset[str]:
    """
    "<ctrl>+<shift>"  -> frozenset({"ctrl", "shift"})
    "<ctrl>+<alt>+j"  -> frozenset({"ctrl", "alt", "j"})
    """
    parts = [p for p in combo.replace(" ", "").split("+") if p]
    return frozenset(normalize_key(p) for p in parts)


# Order modifiers consistently so combos are canonical regardless of press order.
_MODIFIER_ORDER = ["ctrl", "alt", "shift", "cmd"]


def canonical_combo(keys) -> str:
    """
    Turn a set/iterable of canonical key tokens into a stable combo STRING:
        {"shift", "ctrl"}      -> "<ctrl>+<shift>"
        {"ctrl", "alt", "j"}   -> "<ctrl>+<alt>+j"
    Modifiers are ordered (ctrl, alt, shift, cmd); at most one non-modifier key
    is kept (the last-pressed regular key). Pure -> unit testable.
    """
    keys = [normalize_key(k) for k in keys]
    mods = [m for m in _MODIFIER_ORDER if m in keys]
    others = [k for k in keys if k not in _MODIFIER_ORDER]
    ordered = mods + others[:1]
    return "+".join(f"<{k}>" if k in _MODIFIER_ORDER else k for k in ordered)


def pretty_combo(combo: str) -> str:
    """Human-friendly label: '<ctrl>+<shift>' -> 'Ctrl + Shift'."""
    names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win"}
    out = []
    for p in combo.replace(" ", "").split("+"):
        if not p:
            continue
        tok = normalize_key(p)
        out.append(names.get(tok, tok.upper() if len(tok) == 1 else tok.title()))
    return " + ".join(out)


class HotkeyRecorder:
    """
    Captures a key combination as the user physically presses it, for the
    "press to set your hotkey" button.

    Usage (runtime wires these to real key events):
        rec = HotkeyRecorder()
        rec.key_down("ctrl"); rec.key_down("shift")   # user holds keys
        rec.snapshot()      -> "<ctrl>+<shift>"        (current best combo)
        rec.key_up("shift"); rec.key_up("ctrl")
        rec.result          -> "<ctrl>+<shift>"        (locked in on full release)

    We lock the result on the *first release after something was held*, so the
    user just presses the combo and lets go. Pure logic -> unit testable.

    A valid dictation combo needs at least one modifier (so it doesn't fire
    during normal typing); `valid` reflects that.
    """
    def __init__(self):
        self._pressed: list[str] = []      # preserves press order
        self._max_set: list[str] = []      # the fullest combo seen this capture
        self.result: str | None = None
        self.done: bool = False

    def key_down(self, name: str):
        k = normalize_key(name)
        if k not in self._pressed:
            self._pressed.append(k)
        # track the fullest simultaneous combo (by count, then latest)
        if len(self._pressed) >= len(self._max_set):
            self._max_set = list(self._pressed)

    def key_up(self, name: str):
        k = normalize_key(name)
        if k in self._pressed:
            self._pressed.remove(k)
        # lock in when everything is released and we captured something
        if not self._pressed and self._max_set and not self.done:
            self.result = canonical_combo(self._max_set)
            self.done = True

    def snapshot(self) -> str:
        """Current combo string as-held, for live display while capturing."""
        source = self._pressed if self._pressed else self._max_set
        return canonical_combo(source) if source else ""

    @property
    def valid(self) -> bool:
        """A usable combo has >=1 modifier (and ideally a normal key too)."""
        combo = self.result or self.snapshot()
        keys = parse_combo(combo)
        return any(m in keys for m in _MODIFIER_ORDER)

    def reset(self):
        self._pressed.clear()
        self._max_set.clear()
        self.result = None
        self.done = False


@dataclass
class HotkeyMatcher:
    """
    Tracks pressed keys and reports activation edges.

    Call key_down(name)/key_up(name) as events arrive. It returns an event
    string when the *fully-held* state changes:
        "activate"   — combo just became fully held
        "deactivate" — combo just stopped being fully held
        None         — no edge
    """
    combo: frozenset[str]
    _pressed: set[str] = field(default_factory=set)
    _active: bool = False

    @classmethod
    def from_string(cls, combo: str) -> "HotkeyMatcher":
        return cls(combo=parse_combo(combo))

    def _is_satisfied(self) -> bool:
        return self.combo.issubset(self._pressed)

    def key_down(self, name: str) -> str | None:
        self._pressed.add(normalize_key(name))
        if not self._active and self._is_satisfied():
            self._active = True
            return "activate"
        return None

    def key_up(self, name: str) -> str | None:
        self._pressed.discard(normalize_key(name))
        if self._active and not self._is_satisfied():
            self._active = False
            return "deactivate"
        return None

    @property
    def active(self) -> bool:
        return self._active

    def reset(self) -> None:
        self._pressed.clear()
        self._active = False


class HotkeyListener:
    """
    Runtime wrapper around pynput. Imported lazily so the core package can be
    tested on machines without pynput / without a display.
    """
    def __init__(self, combo: str, on_activate, on_deactivate):
        self.matcher = HotkeyMatcher.from_string(combo)
        self.on_activate = on_activate
        self.on_deactivate = on_deactivate
        self._listener = None

    def _key_name(self, key) -> str:
        # pynput Key vs KeyCode
        try:
            from pynput import keyboard
        except ImportError:
            return str(key)
        if isinstance(key, keyboard.Key):
            return key.name
        if isinstance(key, keyboard.KeyCode) and key.char:
            return key.char
        return str(key)

    def _on_press(self, key):
        evt = self.matcher.key_down(self._key_name(key))
        if evt == "activate":
            self.on_activate()

    def _on_release(self, key):
        evt = self.matcher.key_up(self._key_name(key))
        if evt == "deactivate":
            self.on_deactivate()

    def start(self):
        if self._listener is not None:
            return  # already running — avoid a duplicate listener
        from pynput import keyboard
        self._listener = keyboard.Listener(
            on_press=self._on_press, on_release=self._on_release
        )
        self._listener.start()

    def stop(self):
        if self._listener:
            self._listener.stop()
            self._listener = None

    def set_combo(self, combo: str):
        was_running = self._listener is not None
        self.stop()
        self.matcher = HotkeyMatcher.from_string(combo)
        if was_running:
            self.start()
