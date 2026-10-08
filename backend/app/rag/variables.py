"""Variables answer questions directly.

A variable such as `Course_fee = 11500` (description "কোয়ান্টাম মেথড কোর্সের ফি") is the business's own, current
value, so a question that asks for it ("Course fee koto?", "কোর্স ফি কত?", "What is the course fee?") is answered
from the variable - before any document, whose copy of the value may be out of date.

A question matches a variable when every word of the variable's name is in the question (English, Banglish or
Bangla - see the synonym list in app.rag.search), or when most of its description is.
"""
from __future__ import annotations

import re

from app.models import Variable
from app.rag.search import _matches, content_words, coverage

MAX_QUESTION_WORDS = 12  # a long question is about more than one value: leave it to the knowledge base / AI
# The organisation's own name adds nothing to "which value?"; every other extra word does ("yoga course fee" is not
# the "Course_fee" variable unless its description says yoga).
NEUTRAL_WORDS = {"quantum", "কোয়ান্টাম", "foundation", "ফাউন্ডেশন", "method", "মেথড", "মেথডের", "your", "apnader",
                 "আপনাদের", "apnar", "আপনার"}


def key_words(key: str) -> list[str]:
    """"Course_fee" / "courseFee" / "course-fee" -> ["course", "fee"]."""
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", key).replace("_", " ").replace("-", " ")
    return content_words(spaced)


def label(v: Variable) -> str:
    return (v.description or "").strip() or " ".join(re.sub(r"([a-z])([A-Z])", r"\1 \2", v.key).replace("_", " ").split()).capitalize()


def match_variable(question: str, variables: list[Variable]) -> tuple[Variable, float] | None:
    q = content_words(question)
    if not q or len(q) > MAX_QUESTION_WORDS:
        return None
    best: tuple[Variable, float] | None = None
    for v in variables:
        if not (v.value or "").strip():
            continue
        score = 0.0
        kw = key_words(v.key)
        if kw:
            m, t = coverage(kw, question)  # every word of the name is asked about
            if m == t:
                score = 1.0 + 0.01 * t     # longer names are more specific: Course_fee_online beats Course_fee
        if v.description:
            dw = content_words(v.description)
            if dw:
                m, t = coverage(dw, question)
                if t and m / t >= 0.7:
                    score = max(score, m / t + 0.01 * m)
        named = set(kw) | set(content_words(v.description or ""))
        extra = [w for w in q if w not in NEUTRAL_WORDS and not _matches(w, named)]
        if score and not extra and (best is None or score > best[1]):
            best = (v, score)
    return best


def variable_answer(v: Variable) -> str:
    return f"{label(v)}: {v.value}"
