"""
corrections.py — the "learn from my corrections" engine.

This is the feature the user specifically asked for: when a transcript comes
out with a wrong word, they fix it in the app, and future transcriptions get
better. Two mechanisms, both of which run locally:

1. LEARNED REPLACEMENTS (post-processing):
   When the user corrects "wisper" -> "Wispr", we store that pair. On every
   future transcript we apply learned replacements before pasting. Applied
   case-insensitively for matching, but we preserve the corrected casing.
   We also do a light fuzzy pass so near-misses ("wispr", "whisper") map too.

2. CUSTOM VOCABULARY (pre-processing / biasing):
   Every corrected *target* word that looks like a name/jargon/unusual term is
   added to a vocabulary list. That list is turned into a Whisper `initial_prompt`
   so the model is biased toward producing those words in the first place.
   This is exactly how real custom-vocabulary STT features work.

Everything is stored as JSON so it's transparent and portable.
"""
from __future__ import annotations
import json
import re
import difflib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Iterable


# Words we don't want to treat as "custom vocabulary" even if corrected.
_COMMON = set("""
the a an and or but if then else of to in on at for with by from into over under
is are was were be been being have has had do does did will would shall should can
could may might must i you he she it we they me him her us them my your his its our their
this that these those here there what which who whom whose when where why how
not no yes so as up down out off just very really too also only even still yet
""".split())

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z''\-]*")


@dataclass
class CorrectionEngine:
    # heard -> meant   (lowercase key)
    replacements: dict[str, str] = field(default_factory=dict)
    # custom vocab words (preserve original casing), stored as a set-like list
    vocabulary: list[str] = field(default_factory=list)
    # how many times each replacement has fired (so we can weight/trust them)
    hit_counts: dict[str, int] = field(default_factory=dict)
    fuzzy_enabled: bool = True
    fuzzy_cutoff: float = 0.86

    # ---------------------------------------------------------------- learning
    def learn_from_edit(self, original: str, corrected: str) -> None:
        """
        Given the original transcript text and the user's corrected version,
        diff them word-by-word and store the substitutions.
        """
        o_words = _tokenize(original)
        c_words = _tokenize(corrected)
        sm = difflib.SequenceMatcher(a=[w.lower() for w in o_words],
                                     b=[w.lower() for w in c_words])
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag == "replace":
                # align 1:1 where we can; also capture whole-phrase swaps
                heard_span = o_words[i1:i2]
                meant_span = c_words[j1:j2]
                if len(heard_span) == len(meant_span):
                    for h, m in zip(heard_span, meant_span):
                        self._add_pair(h, m)
                else:
                    # phrase-level replacement, e.g. "lin works" -> "Linnworks"
                    self._add_phrase(" ".join(heard_span), " ".join(meant_span))

    def _add_pair(self, heard: str, meant: str) -> None:
        h = heard.lower().strip()
        if not h or h == meant.lower():
            return
        self.replacements[h] = meant
        self.hit_counts[h] = self.hit_counts.get(h, 0)
        self._maybe_add_vocab(meant)

    def _add_phrase(self, heard: str, meant: str) -> None:
        h = heard.lower().strip()
        if not h or h == meant.lower():
            return
        self.replacements[h] = meant
        self.hit_counts[h] = self.hit_counts.get(h, 0)
        for w in _tokenize(meant):
            self._maybe_add_vocab(w)

    def _maybe_add_vocab(self, word: str) -> None:
        w = word.strip()
        lw = w.lower()
        if not w or lw in _COMMON or len(w) < 2:
            return
        # Treat as custom vocab if it's capitalised mid-sentence, has internal
        # capitals (CamelCase / brand), or isn't a "normal" lowercase word.
        looks_custom = (
            w[0].isupper()
            or any(c.isupper() for c in w[1:])
            or "-" in w
        )
        if looks_custom and not any(v.lower() == lw for v in self.vocabulary):
            self.vocabulary.append(w)

    def add_vocabulary(self, word: str) -> None:
        """Directly add a custom word (used by the Voice Setup vocab step)."""
        self._maybe_add_vocab(word)
        # force-add even if it looks lowercase-normal, since user asked explicitly
        if not any(v.lower() == word.lower() for v in self.vocabulary):
            self.vocabulary.append(word)

    # -------------------------------------------------------------- applying
    def apply(self, text: str) -> str:
        """Apply learned corrections to a fresh transcript before pasting."""
        if not text:
            return text

        # 1) multi-word phrase replacements first (longest keys first)
        for key in sorted((k for k in self.replacements if " " in k),
                          key=len, reverse=True):
            pattern = re.compile(re.escape(key), re.IGNORECASE)
            if pattern.search(text):
                text = pattern.sub(self.replacements[key], text)
                self.hit_counts[key] = self.hit_counts.get(key, 0) + 1

        # 2) single-word replacements, token by token (preserve punctuation)
        def repl_token(m: re.Match) -> str:
            w = m.group(0)
            lw = w.lower()
            if lw in self.replacements:
                self.hit_counts[lw] = self.hit_counts.get(lw, 0) + 1
                return _match_case(w, self.replacements[lw])
            if self.fuzzy_enabled:
                cand = difflib.get_close_matches(lw, self.replacements.keys(),
                                                 n=1, cutoff=self.fuzzy_cutoff)
                if cand:
                    self.hit_counts[cand[0]] = self.hit_counts.get(cand[0], 0) + 1
                    return _match_case(w, self.replacements[cand[0]])
            return w

        text = _WORD_RE.sub(repl_token, text)
        return text

    def initial_prompt(self, max_words: int = 60) -> str:
        """
        Build a Whisper initial_prompt from the custom vocabulary. Feeding these
        words biases the model toward producing them. We keep it short — an
        overlong prompt hurts more than it helps.
        """
        if not self.vocabulary:
            return ""
        # Prefer the most recently added / most relevant terms.
        words = self.vocabulary[-max_words:]
        return "Vocabulary: " + ", ".join(words) + "."

    # ---------------------------------------------------------------- storage
    def to_dict(self) -> dict:
        return {
            "replacements": self.replacements,
            "vocabulary": self.vocabulary,
            "hit_counts": self.hit_counts,
            "fuzzy_enabled": self.fuzzy_enabled,
            "fuzzy_cutoff": self.fuzzy_cutoff,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "CorrectionEngine":
        return cls(
            replacements=d.get("replacements", {}),
            vocabulary=d.get("vocabulary", []),
            hit_counts=d.get("hit_counts", {}),
            fuzzy_enabled=d.get("fuzzy_enabled", True),
            fuzzy_cutoff=d.get("fuzzy_cutoff", 0.86),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "CorrectionEngine":
        if path.exists():
            try:
                return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))
            except (json.JSONDecodeError, TypeError):
                return cls()
        return cls()


# ----------------------------------------------------------------- helpers
def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text or "")


def _match_case(source: str, target: str) -> str:
    """Make `target` follow the capitalisation style of `source`."""
    if source.isupper():
        return target.upper()
    if source[0].isupper():
        # Title-case only the first letter, keep the rest of target as stored
        return target[0].upper() + target[1:] if target else target
    return target
