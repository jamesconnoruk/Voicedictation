"""
assets.py — resolve paths to bundled asset files.

Works whether running from source (whispr/assets/…) or from a PyInstaller
build (sys._MEIPASS/whispr/assets/…). Returns None if not found so callers
can fall back to drawn graphics.
"""
from __future__ import annotations
import os
import sys


def asset_path(name: str):
    candidates = []
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidates.append(os.path.join(base, "whispr", "assets", name))
    # whispr/core/ -> up one to whispr/, then assets/
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.join(here, "..", "assets", name))
    candidates.append(os.path.join("whispr", "assets", name))
    for c in candidates:
        if os.path.exists(c):
            return c
    return None
