"""Automatic keyword generation for one record (a file chunk, Q&A, variable or program).

Rules that keep the keywords useful and honest:

- Extracted, never invented. Every keyword comes from the record's own words (or is a direct Bangla <-> English
  translation of one of them). AI-suggested keywords are checked against that and dropped when they are not
  grounded in the record ("hallucination guard").
- Scaled to the text: a short record gets a handful of keywords, a long one gets many more, so it can be found by its
  specific details and not only by its headline.
- Distinctive. Words are chosen by rarity in the whole index (tf-idf); a few very rare words become "signature"
  keywords; adjacent words of the question become "phrase" keywords ("course fee" is not "course registration");
  and records that would otherwise look the same get extra "unique" keywords that only they have (find_unique).
"""
from __future__ import annotations

import logging
import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Mapping

from app.rag import search as S
from app.rag.search import content_words

logger = logging.getLogger(__name__)

W_TITLE, W_META, W_DOC, W_BODY = 3.0, 2.5, 1.5, 1.0
W_SIGNATURE, W_PHRASE = 2.0, 3.5
W_BY_ORIGIN = {"manual": 3.0, "ai": 2.8, "unique": 3.0}   # keywords somebody (or the AI / the uniqueness check) added
BODY_BASE, BODY_MAX = 8, 28          # body keywords: 8 + one per 25 words, at most 28
MAX_PHRASES = 6
SIGNATURE_MAX, SIGNATURE_DF_SHARE, SIGNATURE_MIN_RECORDS = 4, 0.03, 20
UNIQUE_MAX = 3
ORIGINS = ("title", "file", "doc", "body", "signature", "phrase", "ai", "manual", "unique")

_BN_SUFFIXES = sorted(["গুলো", "গুলি", "গুলোর", "দের", "রা", "ের", "এর", "কে", "তে", "টি", "টা", "েও", "ে", "র"], key=len, reverse=True)
_META_SPLIT = re.compile(r"[\[\]'\",;/|•·\n]+")
_PLACEHOLDER = re.compile(r"\[[A-Z]+_\d+\]")


def stem_of(term: str) -> str:
    """Inflection-free form, the same function for the index and for the guest's words."""
    t = term.strip().lower()
    if any("ঀ" <= c <= "৿" for c in t):
        for suf in _BN_SUFFIXES:
            if t.endswith(suf) and len(t) - len(suf) >= 2:
                return t[:-len(suf)][:64]
        return t[:64]
    if len(t) > 4 and t.endswith(("sses", "shes", "ches", "xes")):   # classes -> class, but courses -> course
        return t[:-2]
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t[:64]


def terms_of(text: str | None) -> list[str]:
    return content_words(text or "")


def meta_terms(text: str | None) -> list[str]:
    """Topic / keywords written in a data file: "প্রেম/প্রেমে পড়া", "['স্বর্গীয়', 'আকর্ষণ']"."""
    return terms_of(_META_SPLIT.sub(" ", text or ""))


@dataclass
class Kw:
    stem: str
    term: str
    weight: float
    origin: str


def phrase_stems(words: list[str]) -> list[str]:
    """Adjacent word pairs, in order: "course fee" -> "course_fee"."""
    stems = [stem_of(w) for w in words]
    out = []
    for a, b in zip(stems, stems[1:]):
        if a != b and (a + "_" + b) not in out:
            out.append(f"{a}_{b}"[:64])
    return out[:MAX_PHRASES]


def generate_keywords(*, title: str | None = None, meta: str | None = None, doc: str | None = None, body: str | None = None,
                      df: Mapping[str, int] | None = None, n: int = 0) -> list[Kw]:
    """The automatic keywords of one record. `df` = in how many records each stem occurs, `n` = number of records:
    without them (a brand-new index) words are ranked by frequency only."""
    best: dict[str, Kw] = {}

    def put(words: list[str], weight: float, origin: str) -> None:
        for w in words:
            s = stem_of(w)
            if len(s) >= 2 and (s not in best or best[s].weight < weight):
                best[s] = Kw(s, w[:64], weight, origin)

    t_terms = terms_of(title)
    put(t_terms, W_TITLE, "title")
    put(meta_terms(meta), W_META, "file")
    put(terms_of(doc), W_DOC, "doc")

    freq = Counter(terms_of(body))
    k_body = min(BODY_MAX, BODY_BASE + len((body or "").split()) // 25)

    def idf(word: str) -> float:
        return math.log(1 + max(n, 1) / (1 + (df or {}).get(stem_of(word), 0))) if df else 1.0
    ranked = sorted(freq.items(), key=lambda kv: (-(kv[1] * idf(kv[0])), -len(kv[0])))
    put([w for w, _ in ranked[:k_body]], W_BODY, "body")

    if df and n >= SIGNATURE_MIN_RECORDS:   # the rarest words of the record: what only a few records talk about
        limit = max(1, int(SIGNATURE_DF_SHARE * n))
        by_stem = {stem_of(w): w for w in freq}
        rare = sorted((s for s in by_stem if df.get(s, 0) <= limit and len(by_stem[s]) >= 3),
                      key=lambda s: (df.get(s, 0), -freq[by_stem[s]]))
        put([by_stem[s] for s in rare[:SIGNATURE_MAX]], W_SIGNATURE, "signature")

    for p in phrase_stems(t_terms):
        best[p] = Kw(p, p.replace("_", " "), W_PHRASE, "phrase")
    cap = min(90, 28 + len((body or "").split()) // 8)
    words_part = sorted((k for k in best.values() if k.origin != "phrase"), key=lambda k: -k.weight)[:cap]
    return words_part + [k for k in best.values() if k.origin == "phrase"]


def extra_keywords(added: list[dict] | None) -> list[Kw]:
    """Keywords added by hand, by the AI, or by the uniqueness check (kept in record_keywords)."""
    out: list[Kw] = []
    for item in added or []:
        origin = item.get("origin") if item.get("origin") in W_BY_ORIGIN else "manual"
        for w in terms_of(str(item.get("k", ""))):
            s = stem_of(w)
            if len(s) >= 2:
                out.append(Kw(s, w[:64], W_BY_ORIGIN[origin], origin))
    return out


# ---- uniqueness ---------------------------------------------------------------------------------------------------
def find_unique(body_terms: Counter, other_stems: set[str], limit: int = UNIQUE_MAX) -> list[str]:
    """Words of this record that none of the look-alike records contain: the most frequent first."""
    cand = [(freq, w) for w, freq in body_terms.items() if len(w) >= 3 and stem_of(w) not in other_stems]
    cand.sort(key=lambda fw: (-fw[0], -len(fw[1])))
    seen: set[str] = set()
    out: list[str] = []
    for _, w in cand:
        if stem_of(w) not in seen:
            seen.add(stem_of(w))
            out.append(w)
        if len(out) >= limit:
            break
    return out


def look_alikes(topic: set[str], neighbors: Mapping[object, tuple[set[str], set[str]]]) -> list:
    """Records whose topic keywords mostly equal this record's: (topic stems, all stems) per neighbour."""
    if len(topic) < 2:
        return []
    out = []
    for key, (t, _all) in neighbors.items():
        shared = len(topic & t)
        if shared / len(topic) >= 0.75 and shared / max(1, len(t)) >= 0.5:
            out.append(key)
    return out


# ---- AI suggestions, checked against the record ---------------------------------------------------------------------
KEYGEN_SYSTEM = """You write search keywords for ONE record of an organisation's knowledge base (Bangla / English).
Return ONLY JSON: {"keywords": ["..."], "specific": ["..."]}
- keywords: 6 to 12 words or short phrases naming what the record is about. Give each important concept in BOTH Bangla
  script and English (for example "ফি" and "fee").
- specific: 2 to 4 words that make THIS record different from the SIMILAR RECORDS listed (their questions are given).
- Use only words that appear in the record, or their direct Bangla/English translations. Never add a topic the text does
  not mention. No personal data, no [PLACEHOLDERS]."""


def grounded(keyword: str, record_stems: set[str]) -> bool:
    """Every word of the keyword is in the record, or is a direct translation / variant of a word in it."""
    words = terms_of(keyword)
    if not words:
        return False
    for w in words:
        stems = {stem_of(a) for a in S._alternatives(w) if a} | {stem_of(w)}
        if not stems & record_stems:
            return False
    return True


def parse_ai_keywords(raw: str, record_text: str) -> tuple[list[str], list[str]]:
    """(kept, rejected): JSON from the model -> only keywords that are grounded in the record."""
    from app.services.post_reply import parse_json_object
    d = parse_json_object(raw or "") or {}
    candidates = [k.strip() for key in ("keywords", "specific") for k in (d.get(key) or []) if isinstance(k, str)]
    record_stems = {stem_of(w) for w in terms_of(record_text)}
    kept: list[str] = []
    rejected: list[str] = []
    for k in dict.fromkeys(candidates):
        if not (1 < len(k) <= 60) or _PLACEHOLDER.search(k):
            rejected.append(k)
        elif grounded(k, record_stems):
            kept.append(k)
        else:
            rejected.append(k)
    return kept[:24], rejected
