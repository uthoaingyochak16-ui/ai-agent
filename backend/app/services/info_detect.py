"""Split a guest message into pieces and flag the ones that may hold personal details (free, no AI call).

"amar nam Fahim, ami CUET e pori. course fee koto?" ->
    [Piece("amar nam Fahim", True), Piece("ami CUET e pori", True), Piece("course fee koto?", False)]

Only a message with a flagged piece goes to the AI (post_reply.collect_guest_info), which decides per piece whether
it is guest info or a normal question and saves the info. A plain question ("kobe shuru hobe?") costs nothing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.privacy.patterns import EMAIL_RE, PHONE_CAND_RE, ascii_digits

_E = r"(?=[\s,.?!।:;\-]|$)"  # word end that also works after Bangla vowel signs
_B = r"(?<![a-zA-Zঀ-৿])"  # word start
_WORDS = [
    # about me
    "amar", "ami", "amr", "my", "mine", "i am", "i'm", "im", "myself", "আমার", "আমি", "নিজে",
    # name, age
    "nam", "name", "naam", "নাম", "boyosh", "boyos", "boyoss", "বয়স", "বয়স", "age", "years old", "yrs", "bochor", "বছর",
    # where
    "thaki", "thake", "থাকি", "থাকে", "basa", "bashay", "বাসা", "bari", "bari te", "বাড়ি", "thikana", "ঠিকানা", "live", "living",
    "from", "theke", "থেকে", "jela", "জেলা", "district", "upazila", "উপজেলা", "gram", "গ্রাম", "village", "vill",
    # study, work
    "pori", "porhi", "porashona", "পড়ি", "পড়ি", "পড়াশোনা", "class", "ক্লাস", "শ্রেণি", "school", "স্কুল", "college", "কলেজ",
    "madrasa", "মাদ্রাসা", "versity", "varsity", "university", "বিশ্ববিদ্যালয়", "ভার্সিটি", "student", "ছাত্র", "ছাত্রী",
    "year", "semester", "ssc", "hsc", "honours", "masters", "job", "chakri", "চাকরি", "kaj kori", "কাজ করি", "business",
    "byabsa", "ব্যবসা", "teacher", "shikkhok", "শিক্ষক", "doctor", "ডাক্তার", "engineer", "ইঞ্জিনিয়ার", "housewife", "গৃহিণী",
    "profession", "pesha", "পেশা", "office", "অফিস", "company", "কোম্পানি",
    # family
    "ma", "maa", "mayer", "ammu", "ammur", "amma", "abbu", "abbur", "abba", "baba", "babar", "মা", "মায়ের", "আম্মু", "আম্মা",
    "আব্বু", "আব্বা", "বাবা", "বাবার", "bhai", "vai", "bhaiya", "ভাই", "ভাইয়া", "bon", "boner", "apu", "বোন", "আপু", "chele",
    "cheler", "ছেলে", "meye", "meyer", "মেয়ে", "মেয়ে", "wife", "husband", "bou", "বউ", "স্ত্রী", "স্বামী", "shami", "stri",
    "family", "poribar", "পরিবার", "nana", "nani", "dada", "dadi", "নানা", "নানী", "দাদা", "দাদী", "son", "daughter",
    "mother", "father", "brother", "sister", "children", "baccha", "বাচ্চা", "সন্তান", "biye", "বিয়ে", "married", "single",
    # health
    "blood", "রক্ত", "group", "গ্রুপ", "rog", "রোগ", "osukh", "অসুখ", "অসুস্থ", "diabetes", "dayabetis", "ডায়াবেটিস",
    "pressure", "প্রেশার", "heart", "হার্ট", "problem", "somossa", "সমস্যা", "pain", "betha", "batha", "ব্যথা", "asthma",
    "হাঁপানি", "cancer", "ক্যান্সার", "kidney", "কিডনি", "operation", "অপারেশন", "depression", "ডিপ্রেশন", "tension",
    "টেনশন", "ghum", "ঘুম", "insomnia", "migraine", "মাইগ্রেন", "weight", "ওজন",
]
_INFO = re.compile(_B + "(" + "|".join(sorted({re.escape(w).replace(r"\ ", r"\s+") for w in _WORDS}, key=len, reverse=True))
                   + ")" + _E, re.IGNORECASE)
_SPLIT = re.compile(r"(?<=[?!।])\s*|[\n;]+|(?<!Md)(?<!Mr)(?<!Dr)\.(?:\s+|$)|,\s*|\s+(?:and|ebong|এবং|kintu|but|tobe|তবে|r|ar|আর)\s+(?=\S)", re.IGNORECASE)
_QUESTION = re.compile(r"(?i)\?|" + _B + r"(koto|kto|kobe|kothay|kivabe|kibhabe|keno|ki|কত|কবে|কোথায়|কীভাবে|কিভাবে|কেন|কি|কী|"
                       r"what|when|where|how|why|which|can|is there|do you)" + _E)


@dataclass(frozen=True)
class Piece:
    text: str
    info: bool      # a keyword / contact pattern that may carry personal details
    question: bool  # looks like a question


def split(text: str) -> list[Piece]:
    out: list[Piece] = []
    for raw in _SPLIT.split(text or ""):
        t = re.sub(r"\s+", " ", raw or "").strip(" ,")
        if not t or not re.search(r"\w", t):
            continue
        norm = ascii_digits(t)
        info = bool(_INFO.search(t) or EMAIL_RE.search(norm) or PHONE_CAND_RE.search(norm))
        out.append(Piece(t[:300], info, bool(_QUESTION.search(t))))
    return out[:20]


def worth_checking(pieces: list[Piece]) -> bool:
    """At least one piece may carry personal details (the AI then decides which pieces really do)."""
    return any(p.info for p in pieces)
