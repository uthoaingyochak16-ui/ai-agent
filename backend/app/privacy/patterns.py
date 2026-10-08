"""Shared detection patterns (used by redaction AND identity extraction).

All detection runs on a digit-normalized copy of the text (Bengali digits -> ASCII, 1:1 so spans align).
Regex detection is heuristic: it reduces leakage, it cannot guarantee zero leakage (see README).
"""
from __future__ import annotations

import re

_BN = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")


def ascii_digits(s: str) -> str:
    return s.translate(_BN)


EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
PHONE_CAND_RE = re.compile(r"(?<![\w])\+?\d(?:[\s().\-]?\d){7,18}(?!\w)")
CARD_CAND_RE = re.compile(r"(?<![\w])\d(?:[ \-]?\d){12,18}(?!\w)")
NID_CONTEXT_RE = re.compile(
    r"(?i)(?:\bnid\b|national\s*id|এনআইডি|জাতীয়\s*পরিচয়(?:\s*পত্র)?)[^\d\n]{0,15}(\d[\d \-]{8,20}\d)")
NID_BARE_RE = re.compile(r"(?<![\w])\d{17}(?!\w)")  # legacy BD NID length; shorter forms need context
ADDRESS_RE = re.compile(
    r"(?i)(?:house|flat|road|block|sector|ward|apartment|street|lane|avenue|বাসা|বাড়ি|ফ্ল্যাট|রোড|ব্লক|সেক্টর)"
    r"\s*(?:no\.?|#|নং)?\s*[:\-]?\s*[\w/\-]+(?:\s*,\s*[^,\n.;]{2,40}){0,4}")

CARD_MIN, CARD_MAX = 13, 19


def luhn_ok(digits: str) -> bool:
    if not digits.isdigit():
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def digits_only(s: str) -> str:
    return re.sub(r"\D", "", s)
