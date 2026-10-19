"""The standard Q&A categories (Q&A manager -> Known Q&A) and how older / free-typed names map onto them.

`standard_category` turns any stored or typed name into one of CATEGORIES (unknown names -> "Other"), so the
category filter stays small and every Q&A sits in exactly one place. `guess_category` suggests a category for a new
question (Unknown questions queue) from its words: Bangla, English and Banglish.
"""
from __future__ import annotations

import re

CATEGORIES = ("General", "Contact & Address", "Courses", "Meditation", "Blood Donation", "Quantum Lab", "Registration",
              "Fees & Payment", "Programs & Events", "Complaints", "Other")

# older category names (lower-case) -> standard. Used once by migration 0025 and for every save / import since.
ALIASES = {
    "general": "General", "about": "General", "org": "General", "misc": "General", "privacy": "General",
    "greeting": "General",
    "contact": "Contact & Address", "location": "Contact & Address", "address": "Contact & Address",
    "contacts": "Contact & Address", "contact & address": "Contact & Address", "branch": "Contact & Address",
    "course": "Courses", "courses": "Courses", "eligibility": "Courses",
    "method": "Meditation", "meditation": "Meditation",
    "blood": "Blood Donation", "blood donation": "Blood Donation",
    "lab": "Quantum Lab", "quantum lab": "Quantum Lab",
    "registration": "Registration",
    "fee": "Fees & Payment", "fees": "Fees & Payment", "payment": "Fees & Payment", "fees & payment": "Fees & Payment",
    "events": "Programs & Events", "event": "Programs & Events", "programs": "Programs & Events",
    "program": "Programs & Events", "programs & events": "Programs & Events",
    "complaint": "Complaints", "complaints": "Complaints",
    "support": "Other", "service": "Other", "partner": "Other", "books": "Other", "tech": "Other", "other": "Other",
}
_BY_LOWER = {c.lower(): c for c in CATEGORIES}
KNOWN_NAMES = frozenset(_BY_LOWER) | frozenset(ALIASES)  # names that map by name; others are guessed from the question

# word hints -> category, checked in this order (the first hit wins). Bangla words are matched as substrings
# (inflections: রক্তের, ঠিকানায়), Latin ones as whole words.
_HINTS: list[tuple[str, tuple[str, ...]]] = [
    ("Quantum Lab", ("ল্যাব", "lab", "laboratory", "ক্রসম্যাচ", "crossmatch")),
    ("Blood Donation", ("রক্ত", "ব্লাড", "blood", "rokto", "rakto", "donor", "ডোনার", "রক্তদান")),
    ("Contact & Address", ("ঠিকানা", "অফিস", "শাখা", "সেন্টার", "যোগাযোগ", "ফোন", "নম্বর", "নাম্বার", "লোকেশন", "কোথায়",
                           "address", "thikana", "office", "branch", "center", "centre", "contact", "phone", "number",
                           "nambar", "location", "kothay", "jogajog", "hotline", "email", "map")),
    ("Fees & Payment", ("ফি", "টাকা", "খরচ", "দাম", "পেমেন্ট", "বিকাশ", "fee", "fees", "taka", "cost", "price", "payment",
                        "pay", "bkash", "nagad", "khoroch", "dam", "refund")),
    ("Registration", ("রেজিস্ট্রেশন", "ভর্তি", "নিবন্ধন", "registration", "register", "enrol", "enroll", "admission",
                      "bhorti", "form")),
    ("Programs & Events", ("প্রোগ্রাম", "ইভেন্ট", "অনুষ্ঠান", "সেমিনার", "সাদাকায়ন", "program", "programme", "event",
                           "seminar", "sadakaion", "schedule", "kobe")),
    ("Courses", ("কোর্স", "ক্লাস", "ব্যাচ", "course", "class", "batch", "kors")),
    ("Meditation", ("মেডিটেশন", "ধ্যান", "মেথড", "meditation", "method", "dhyan")),
    ("Complaints", ("অভিযোগ", "সমস্যা", "complain", "complaint", "problem", "obhijog", "issue")),
]


def standard_category(raw: str | None) -> str | None:
    """Any category name -> one of CATEGORIES; None stays None (the editor may leave it empty)."""
    if raw is None or not str(raw).strip():
        return None
    low = str(raw).strip().lower()
    return _BY_LOWER.get(low) or ALIASES.get(low) or "Other"


def guess_category(text: str) -> str:
    t = (text or "").lower()
    words = set(re.findall(r"[a-z]+", t))
    for cat, hints in _HINTS:
        for h in hints:
            if (h in words) if h.isascii() else (h in t):
                return cat
    return "General"


def migrate_category(old: str | None, question: str, answer: str) -> str:
    """Migration 0025: blood / lab questions get their own category whatever they were filed under; the rest map by
    name (the old name is kept in qa_category_backup, so this is reversible)."""
    q = (question or "").lower()
    guess = guess_category(q)
    if guess in ("Blood Donation", "Quantum Lab"):
        return guess
    return standard_category(old) or guess
