"""
transcripts.py — persistent library of dictations.

Every time the user finishes a push-to-talk dictation, we store it as its own
entry (id, timestamp, raw text, corrected/current text, target app). The UI
shows these as separate transcript cards. Editing a card's text triggers the
correction engine to learn.

Backed by a single JSON-lines file for simplicity and durability (append-only
writes, full-rewrite on edit/delete).
"""
from __future__ import annotations
import json
import time
import uuid
from pathlib import Path
from dataclasses import dataclass, asdict, field


@dataclass
class Transcript:
    id: str
    created_at: float
    raw_text: str            # exactly what Whisper produced (+ learned corrections)
    text: str                # current text (may have been hand-edited by user)
    duration_s: float = 0.0
    target_app: str = ""     # window title we pasted into, if known
    edited: bool = False

    @staticmethod
    def new(raw_text: str, text: str | None = None,
            duration_s: float = 0.0, target_app: str = "") -> "Transcript":
        return Transcript(
            id=uuid.uuid4().hex[:12],
            created_at=time.time(),
            raw_text=raw_text,
            text=text if text is not None else raw_text,
            duration_s=duration_s,
            target_app=target_app,
        )


class TranscriptStore:
    def __init__(self, path: Path):
        self.path = path
        self._items: list[Transcript] = []
        self._load()

    def _load(self) -> None:
        self._items = []
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    self._items.append(Transcript(**d))
                except (json.JSONDecodeError, TypeError):
                    continue  # skip corrupt line, keep going

    def _rewrite(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            for t in self._items:
                f.write(json.dumps(asdict(t)) + "\n")

    # ------------------------------------------------------------- operations
    def add(self, t: Transcript) -> None:
        self._items.append(t)
        # append-only fast path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(t)) + "\n")

    def all(self, newest_first: bool = True) -> list[Transcript]:
        items = list(self._items)
        items.sort(key=lambda t: t.created_at, reverse=newest_first)
        return items

    def get(self, tid: str) -> Transcript | None:
        return next((t for t in self._items if t.id == tid), None)

    def update_text(self, tid: str, new_text: str) -> Transcript | None:
        t = self.get(tid)
        if t is None:
            return None
        t.text = new_text
        t.edited = True
        self._rewrite()
        return t

    def delete(self, tid: str) -> bool:
        before = len(self._items)
        self._items = [t for t in self._items if t.id != tid]
        if len(self._items) != before:
            self._rewrite()
            return True
        return False

    def search(self, query: str) -> list[Transcript]:
        q = query.lower().strip()
        if not q:
            return self.all()
        return [t for t in self.all() if q in t.text.lower()]

    def __len__(self) -> int:
        return len(self._items)
