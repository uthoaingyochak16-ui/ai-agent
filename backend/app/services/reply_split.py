"""Split a long reply into several chat messages of at most N lines each, so the guest is not handed a wall of text.

"Lines" are what the guest sees on a phone: a written line, and a long sentence counts as several (LINE_CHARS each).
Paragraphs stay whole when they fit; a long paragraph is cut between lines, then between sentences, never in a word.
"""
from __future__ import annotations

import math
import re

LINE_CHARS = 60  # roughly one line of a chat bubble on a phone
_SENTENCE_END = re.compile(r"(?<=[.!?।])\s+")


def visual_lines(text: str) -> int:
    return sum(max(1, math.ceil(len(line) / LINE_CHARS)) for line in text.split("\n") if line.strip())


def _pieces(text: str, max_lines: int) -> list[tuple[str, str]]:
    """[(joiner before it, piece)]: paragraphs ("\\n\\n"), lines of a long paragraph ("\\n"), sentences or word runs
    of a long line (" "). Each piece fits in max_lines."""
    out: list[tuple[str, str]] = []
    for para in (p.strip() for p in re.split(r"\n\s*\n", text.strip())):
        if not para:
            continue
        if visual_lines(para) <= max_lines:
            out.append(("\n\n", para))
            continue
        for li, line in enumerate(para.split("\n")):
            first_join = "\n\n" if li == 0 else "\n"
            if visual_lines(line) <= max_lines:
                out.append((first_join, line))
                continue
            for si, sentence in enumerate(_SENTENCE_END.split(line)):
                join = first_join if si == 0 else " "
                if visual_lines(sentence) <= max_lines:
                    out.append((join, sentence))
                    continue
                cur = ""
                for w in sentence.split():  # one endless sentence: cut between words
                    if cur and visual_lines(f"{cur} {w}") > max_lines:
                        out.append((join, cur))
                        join, cur = " ", w
                    else:
                        cur = f"{cur} {w}".strip()
                if cur:
                    out.append((join, cur))
    return out


def split_reply(text: str, max_lines: int = 5) -> list[str]:
    """[text] when it is short enough (6 lines still go as one) or max_lines <= 0; otherwise parts of at most
    max_lines lines each, in order."""
    if max_lines <= 0 or visual_lines(text) <= max_lines + 1:
        return [text]
    parts: list[str] = []
    cur = ""
    for join, piece in _pieces(text, max_lines):
        candidate = f"{cur}{join}{piece}" if cur else piece
        if cur and visual_lines(candidate) > max_lines:
            parts.append(cur)
            cur = piece
        else:
            cur = candidate
    if cur:
        parts.append(cur)
    return [p for p in parts if p.strip()] or [text]
