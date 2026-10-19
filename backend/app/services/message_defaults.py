"""Defaults for everything on AI Settings -> 'Messages and handover' and 'Small talk', read from one file:
app/defaults/messages_and_handover.json (sections for readability; greeting words grouped by kind).

Edit that file to change what a fresh install / a reset starts with. A value changed in AI Settings is stored in the
database and takes priority over the file.
"""
from __future__ import annotations

import json
from pathlib import Path

PATH = Path(__file__).resolve().parent.parent / "defaults" / "messages_and_handover.json"


def load(path: Path = PATH) -> dict:
    doc = json.loads(path.read_text(encoding="utf-8"))
    out: dict = {}
    for section, values in doc.items():
        if section.startswith("_"):
            continue
        for key, value in values.items():
            if key == "greeting_words" and isinstance(value, dict):   # {"islamic": [...], "time_of_day": [...]}
                value = list(dict.fromkeys(w for group in value.values() for w in group))
            out[key] = value
    return out


MESSAGE_DEFAULTS = load()
