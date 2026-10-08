"""Banglish <-> Bangla matching by sound.

Guests often type Bangla in Latin letters ("Islam e prem kora ki jayez?"). Both scripts are reduced to the same
consonant "sound key" (প্রেম -> prem -> "prm", "prem" -> "prm"; ভ্যালেন্টাইন -> "blntn", "valentine" -> "blntn"), so a
Banglish word can be matched to the Bangla words the knowledge base actually uses.
"""
from __future__ import annotations

import re

_VOWELS = {"অ": "a", "আ": "a", "ই": "i", "ঈ": "i", "উ": "u", "ঊ": "u", "ঋ": "ri", "এ": "e", "ঐ": "oi", "ও": "o", "ঔ": "ou",
           "া": "a", "ি": "i", "ী": "i", "ু": "u", "ূ": "u", "ৃ": "ri", "ে": "e", "ৈ": "oi", "ো": "o", "ৌ": "ou"}
_CONS = {"ক": "k", "খ": "kh", "গ": "g", "ঘ": "gh", "ঙ": "ng", "চ": "ch", "ছ": "chh", "জ": "j", "ঝ": "jh", "ঞ": "n",
         "ট": "t", "ঠ": "th", "ড": "d", "ঢ": "dh", "ণ": "n", "ত": "t", "থ": "th", "দ": "d", "ধ": "dh", "ন": "n",
         "প": "p", "ফ": "f", "ব": "b", "ভ": "bh", "ম": "m", "য": "j", "র": "r", "ল": "l", "শ": "sh", "ষ": "sh", "স": "s",
         "হ": "h", "ড়": "r", "ঢ়": "r", "য়": "y", "ৎ": "t", "ং": "ng", "ঃ": "h"}
_SILENT = {"্", "ঁ", "়", "‌", "‍"}


def romanize(word: str) -> str:
    """প্রেম -> prem. Inherent vowels are left out; the sound key ignores vowels anyway."""
    out = []
    for i, ch in enumerate(word):
        if ch == "য" and i and word[i - 1] == "্":  # য-ফলা (ভ্যা) sounds like "y", not "j"
            out.append("y")
        elif ch in _CONS:
            out.append(_CONS[ch])
        elif ch in _VOWELS:
            out.append(_VOWELS[ch])
        elif ch in _SILENT:
            continue
        elif ch.isascii() and ch.isalnum():
            out.append(ch.lower())
    return "".join(out)


_DIGRAPHS = [("chh", "c"), ("ch", "c"), ("sh", "s"), ("ph", "f"), ("bh", "b"), ("kh", "k"), ("gh", "g"),
             ("th", "t"), ("dh", "d"), ("jh", "j"), ("ck", "k"), ("v", "b"), ("w", "b"), ("z", "j"), ("q", "k"),
             ("x", "ks")]


def sound_key(latin: str) -> str:
    """Consonant skeleton of a Latin-script word: 'valentine' -> 'blntn', 'islam' -> 'aslm'."""
    s = re.sub(r"[^a-z]", "", latin.lower())
    if not s:
        return ""
    for a, b in _DIGRAPHS:
        s = s.replace(a, b)
    head = "a" if s[0] in "aeiouy" else s[0]
    body = re.sub(r"[aeiouy]", "", s[1:])
    return re.sub(r"(.)\1+", r"\1", head + body)


def is_bangla(word: str) -> bool:
    return any("ঀ" <= c <= "৿" for c in word)


def build_keymap(words_by_df: list[str]) -> dict[str, list[str]]:
    """sound key -> Bangla corpus words (most common first)."""
    km: dict[str, list[str]] = {}
    for w in words_by_df:
        if not is_bangla(w):
            continue
        k = sound_key(romanize(w))
        if len(k) >= 3:
            lst = km.setdefault(k, [])
            if len(lst) < 6:
                lst.append(w)
    return km
