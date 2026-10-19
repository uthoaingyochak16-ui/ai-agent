"""Hybrid knowledge-base search and short extractive answers.

Vector similarity alone misses a lot - especially with the offline `hash` embedder and Bangla text - so every
question is also scored lexically (BM25 over words + in-word character trigrams, which tolerates Bangla
inflections such as হৃদরোগী / হৃদরোগীরা). A chunk counts as relevant only when enough of the question's content words
appear in it, its record question closely matches, or its vector similarity reaches the admin's threshold.

`extractive_answer` builds a short reply straight from the best chunk when no AI model is available.
"""
from __future__ import annotations

import math
import logging
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Chunk, Document
from app.rag.embeddings import embedding_signature
from app.rag.translit import build_keymap, sound_key
from app.rag.rerank import rerank_texts
from app.services.settings_service import get_setting

logger = logging.getLogger(__name__)

# Words that carry no topic ("what is the ...", "কি", "কী", "কেন"); kept short on purpose.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "in", "on", "for", "and", "or", "with", "it", "this",
    "that", "what", "which", "who", "how", "why", "when", "where", "do", "does", "did", "can", "could", "i", "you", "we",
    "me", "my", "your", "our", "please", "tell", "about", "any", "there", "have", "has", "will", "would", "should",
    "ki", "koto", "kivabe", "keno", "kothay", "kothai", "kothae", "kuthay", "kotai", "kobe", "ache", "ase", "achhe", "hobe", "hoy", "er", "ar", "ta", "ti",
    "niya", "niye", "nia", "kaj", "kaaj",
    "na", "ami", "apni", "amar", "apnar", "tumi", "ei", "oi", "jodi", "bolen", "bolun", "janate", "jante", "chai",
    "কি", "কী", "কে", "কেন", "কোথায়", "কখন", "কিভাবে", "কীভাবে", "কত", "এর", "ও", "এবং", "আর", "বা", "এই", "সেই", "এটা",
    "ওই", "যে", "যা", "তা", "না", "হয়", "হবে", "হয়েছে", "আছে", "ছিল", "করে", "করা", "করতে", "কোনো", "কোন", "আমি", "আপনি",
    "আমার", "আপনার", "আমাদের", "তুমি", "জানতে", "চাই", "বলুন", "দয়া", "করে", "সত্যিই", "নিয়ে", "জন্য", "jonno", "jnno", "jonne", "hote", "hobo", "lage", "lagey", "থেকে", "দিয়ে",
}
STOPWORDS |= {"করব", "করবো", "করবেন", "করি", "হবো", "হব", "যাবে", "যাবো", "যায়", "পারবো", "পারব", "পারবে", "পারবেন",
              "দিবো", "দেব", "দিতে", "নেব", "নিতে", "পাবো", "পাব", "পাবেন", "পাওয়া", "den", "din", "dao", "diben", "deben", "dibe", "janaben", "janan",
              "dorkar", "lagbe", "দিন", "দেন", "দাও", "দিবেন", "জানাবেন", "জানান", "দরকার", "kora", "kore", "korle", "korben", "korte",
              "hole", "hoile", "hoye", "thik", "ache", "acche", "achhe", "many", "much", "does", "is", "are", "the", "a",
              "e", "te", "er", "ta", "ti", "to", "for", "of", "kemne", "kibhabe", "kivabe", "kno", "keno", "jodi",
              "thake", "thaken", "thaka", "kora", "korechen", "dey", "den", "থাকে", "থাকেন", "দেয়", "দেন",
              "prodhan", "prodan", "provide", "provides", "প্রদান", "ছিলেন", "ছিলো", "হয়েছিলেন", "হলেন", "হন", "আপনাদের", "ধরনের", "কয়", "কেমন", "উচিত", "লাগবে", "kore", "korbo"}

# Same meaning, different words / scripts: an English or Banglish question still finds the Bangla text, and
# "ভর্তি" finds "রেজিস্ট্রেশন". Kept to the organisation's own vocabulary; extend as new questions show up.
SYNONYMS: list[set[str]] = [
    {"quantum", "kuantam", "কোয়ান্টাম"}, {"foundation", "ফাউন্ডেশন"}, {"method", "মেথড"},
    {"course", "courses", "kors", "কোর্স"},
    {"fee", "fees", "cost", "price", "charge", "khoroch", "ফি", "খরচ", "দাম", "মূল্য"},
    {"meditation", "meditate", "dhyan", "মেডিটেশন", "ধ্যান"}, {"yoga", "ইয়োগা", "যোগব্যায়াম"},
    {"heart", "hridrog", "হার্ট", "হৃদরোগ"}, {"club", "ক্লাব"},
    {"blood", "rokto", "রক্ত", "রক্তদান"}, {"donate", "donation", "donor", "dan", "দান", "অনুদান", "দাতা"},
    {"zakat", "যাকাত"}, {"school", "college", "স্কুল", "কলেজ", "বিদ্যালয়"}, {"cosmo", "কসমো"},
    {"branch", "branches", "center", "centre", "shakha", "sakha", "শাখা", "সেন্টার", "সেল"},
    {"address", "location", "thikana", "ঠিকানা", "অবস্থান", "লোকেশন", "lokeshon", "place", "স্থান", "jayga", "jaiga", "জায়গা"}, {"office", "অফিস", "দফতর", "কার্যালয়"},
    {"phone", "mobile", "contact", "number", "ফোন", "মোবাইল", "যোগাযোগ", "নম্বর"},
    {"register", "registration", "admission", "admit", "enroll", "join", "bhorti", "vorti", "ভর্তি", "রেজিস্ট্রেশন", "নিবন্ধন",
     "apply", "application", "abedon", "আবেদন", "যোগদান"},
    {"requirement", "requirements", "eligibility", "eligible", "qualification", "joggota", "jogyota", "যোগ্যতা",
     "প্রয়োজনীয়তা"},
    {"language", "bhasha", "vasha", "basha", "ভাষা", "ভাষায়"},
    {"founder", "guruji", "প্রতিষ্ঠাতা", "গুরুজী", "গুরুজি", "মহাজাতক"}, {"app", "অ্যাপ", "এপ"},
    {"burial", "funeral", "dafon", "দাফন", "সৎকার"}, {"age", "boyosh", "বয়স"},
    {"benefit", "benefits", "upokar", "উপকার", "উপকারিতা", "লাভ"}, {"religion", "ধর্ম", "ধর্মের"},
    {"schedule", "batch", "next", "upcoming", "ব্যাচ", "পরবর্তী", "আসন্ন", "সময়সূচি", "তারিখ"},
    {"teach", "taught", "learn", "shekhay", "shikhte", "শেখানো", "শেখা", "শিখতে", "শেখায়"},
    {"what", "ki", "কী", "কি"},  # removed as stopwords anyway; listed so they never become "unknown rare words"
    {"service", "services", "seba", "sheba", "saba", "sebamulok", "সেবা", "সেবামূলক", "সেবাসমূহ"},
    {"provide", "provides", "offer", "prodhan", "prodan", "প্রদান"}, {"activity", "activities", "karjokrom", "কার্যক্রম"},
    {"dhaka", "dacca", "ঢাকা"}, {"khulna", "খুলনা"}, {"jessore", "jashore", "jossor", "যশোর"},
    {"chattogram", "chittagong", "chottogram", "চট্টগ্রাম"}, {"rajshahi", "রাজশাহী"}, {"sylhet", "sileth", "সিলেট"},
    {"rangpur", "রংপুর"}, {"barishal", "barisal", "বরিশাল"}, {"mymensingh", "ময়মনসিংহ"}, {"cumilla", "comilla", "কুমিল্লা"},
    {"bogura", "bogra", "বগুড়া"}, {"gazipur", "গাজীপুর"}, {"narayanganj", "নারায়ণগঞ্জ"},
    {"day", "days", "din", "দিন", "দিনের", "দিনব্যাপী"}, {"love", "prem", "valobasa", "bhalobasa", "প্রেম", "ভালবাসা", "ভালোবাসা"},
    {"marriage", "marry", "wedding", "biye", "bie", "বিয়ে", "বিবাহ"}, {"friend", "friendship", "bondhu", "বন্ধু", "বন্ধুত্ব"},
    {"breakup", "separation", "সম্পর্কচ্ছেদ", "বিচ্ছেদ"}, {"relationship", "somporko", "shomporko", "সম্পর্ক"},
    {"islam", "islamic", "ইসলাম", "ইসলামি"}, {"religion", "dhormo", "ধর্ম", "ধর্মীয়"},
    {"student", "students", "chatro", "chatri", "ছাত্র", "ছাত্রী", "ছাত্রছাত্রী", "শিক্ষার্থী"},
    {"girl", "daughter", "meye", "মেয়ে", "মেয়েসন্তান"}, {"boy", "son", "chele", "ছেলে", "ছেলেসন্তান"},
    {"parents", "guardian", "abba", "amma", "বাবা-মা", "অভিভাবক", "আব্বা-আম্মা"},
    {"study", "studies", "porashona", "porasuna", "পড়াশোনা", "লেখাপড়া"}, {"exam", "porikkha", "পরীক্ষা"},
    {"anger", "rag", "রাগ"}, {"tension", "stress", "anxiety", "দুশ্চিন্তা", "টেনশন"}, {"sleep", "ghum", "ঘুম"},
    {"depression", "hotasha", "হতাশা"}, {"health", "sastho", "স্বাস্থ্য", "সুস্থতা"}, {"job", "chakri", "চাকরি"},
    {"money", "taka", "টাকা", "অর্থ"}, {"prayer", "namaz", "namaj", "নামাজ"}, {"dua", "doa", "দোয়া"},
]
_SYN: dict[str, frozenset[str]] = {}
for _group in SYNONYMS:
    _g = frozenset(unicodedata.normalize("NFC", w.lower()) for w in _group)
    for _w in _g:
        _SYN[_w] = _g


# Banglish -> Bangla by sound, built from the words the knowledge base really uses (see app.rag.translit)
_KEYMAP: dict[str, list[str]] = {}


def _alternatives(word: str) -> frozenset[str]:
    alts = _SYN.get(word, frozenset((word,)))
    if word.isascii() and word.isalpha() and len(word) >= 4 and _KEYMAP:
        key = sound_key(word)
        found = _KEYMAP.get(key) if len(key) >= 3 else None
        if found:
            alts = alts | frozenset(found[:4])
    return alts


def _bangla_of(word: str) -> str:
    """The Bangla member of a word's synonym group (for comparing an English question with a Bangla one)."""
    return next((w for w in sorted(_alternatives(word)) if any("ঀ" <= c <= "৿" for c in w)), word)


RECORD_QUESTION_MIN = 0.6  # share of the guest's (weighted) words that a quoted record's question must contain
_SPLIT_SENTENCES = re.compile(r"(?<=[।!?.])\s+|\n+")
_HEAD = re.compile(r"^(?:প্রশ্ন|Question): .*?\n(?:উত্তর|Answer): ", re.DOTALL)


def norm(text: str) -> str:
    """Lowercase, NFC, punctuation -> space; keeps Bangla vowel signs (combining marks) intact."""
    t = unicodedata.normalize("NFC", (text or "").lower())
    t = "".join(c if c.isspace() or unicodedata.category(c)[0] in "LMN" else " " for c in t)
    return re.sub(r"\s+", " ", t).strip()


def content_words(text: str) -> list[str]:
    return [w for w in norm(text).split() if w not in STOPWORDS and (len(w) > 1 or w.isdigit())]


def _features(words: list[str]) -> list[str]:
    out = list(words)
    for w in words:
        if len(w) >= 4:
            p = f"#{w}#"
            out += [f"~{p[i:i + 3]}" for i in range(len(p) - 2)]
    return out


def _stem_match(q: str, words: set[str]) -> bool:
    """Exact, or the same stem (Bangla/English inflection: শিক্ষার্থী / শিক্ষার্থীরা, course / courses)."""
    if q in words:
        return True
    if len(q) < 4:
        return False
    stem = q[: max(3, len(q) - 2)]
    return any(w.startswith(stem) or (len(w) >= 4 and q.startswith(w[: max(3, len(w) - 2)])) for w in words)


def _matches(q: str, words: set[str]) -> bool:
    return any(_stem_match(a, words) for a in _alternatives(q))


def _idf_of(q: str, idf: dict[str, float], unknown: float) -> float:
    """A word's rarity; an English/Banglish word the Bangla texts never use takes its Bangla synonym's rarity."""
    known = [idf[a] for a in _alternatives(q) if a in idf]
    if known:
        return min(known)
    if q.isascii():  # a Latin word with no Bangla counterpart is usually Banglish filler, not the topic
        return unknown * 0.4
    return unknown


def coverage(query_words: list[str], text: str) -> tuple[int, int]:
    """(matched, total) content words of the query found in the text."""
    words = set(content_words(text))
    uniq = list(dict.fromkeys(query_words))
    return sum(1 for q in uniq if _matches(q, words)), len(uniq)


def enough_coverage(matched: int, total: int) -> bool:
    if total == 0:
        return False
    need = 1 if total == 1 else max(2, math.ceil(total * 0.5))
    return matched >= min(total, need)


def question_similarity(a: str, b: str | None) -> float:
    if not b:
        return 0.0
    x, y = norm(a), norm(b)
    if not x or not y:
        return 0.0
    translated = " ".join(_bangla_of(w) for w in x.split())
    return max(SequenceMatcher(None, x, y).ratio(), SequenceMatcher(None, translated, y).ratio())


# ---- BM25 over the whole knowledge base, cached per context_version ---------------------------------------------
class LexicalIndex:
    K1, B = 1.4, 0.75

    def __init__(self, docs: list[tuple[int, str]]):
        self.ids = [i for i, _ in docs]
        self.tf = [Counter(_features(content_words(t))) for _, t in docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {f: math.log(1 + (n - d + 0.5) / (d + 0.5)) for f, d in df.items()}

    def search(self, query: str, limit: int) -> list[tuple[int, float]]:
        q = Counter(_features([a for w in content_words(query) for a in _alternatives(w)]))
        if not q:
            return []
        scores = []
        for idx, tf in enumerate(self.tf):
            s = 0.0
            for f, qn in q.items():
                n = tf.get(f)
                if n:
                    w = 1.0 if not f.startswith("~") else 0.35  # whole words count more than trigrams
                    s += w * self.idf.get(f, 0) * n * (self.K1 + 1) / (n + self.K1 * (1 - self.B + self.B * self.len[idx] / self.avg))
            if s > 0:
                scores.append((self.ids[idx], s))
        scores.sort(key=lambda x: -x[1])
        return scores[:limit]


_INDEX: dict[str, tuple[int, LexicalIndex]] = {}


async def lexical_index(session: AsyncSession) -> LexicalIndex:
    version = int(await get_setting(session, "context_version") or 1)
    bind = session.bind
    key = str(getattr(getattr(bind, "engine", bind), "url", "default"))  # an engine, or a connection (Test Chat no-save)
    cached = _INDEX.get(key)
    if cached and cached[0] == version:
        return cached[1]
    rows = (await session.execute(select(Chunk.id, Chunk.question, Chunk.content).join(Document, Chunk.document_id == Document.id)
                                  .where(Document.status == "ready"))).all()
    idx = LexicalIndex([(i, f"{q or ''} {q or ''} {c}") for i, q, c in rows])
    _INDEX[key] = (version, idx)
    words = sorted((f for f in idx.idf if not f.startswith("~")), key=lambda f: idx.idf[f])  # common words first
    _KEYMAP.clear()
    _KEYMAP.update(build_keymap(words))
    return idx


@dataclass
class Hit:
    chunk: Chunk
    document: Document
    score: float
    vector: float
    lexical: float
    matched: int
    total: int
    question_sim: float
    weighted: float = 0.0  # share of the question's meaning (IDF-weighted words) found in the chunk
    rerank: float | None = None
    text: str | None = None   # full record text for the prompt (keyword search joins a Q&A record's chunks); default: chunk.content


def weighted_coverage(query_words: list[str], text: str, idf: dict[str, float], unknown: float) -> float:
    """Like coverage(), but each word counts by rarity. A rare word that is missing (e.g. "সিলেট" when the chunk
    is about Rangpur, or "আবহাওয়া" that no document mentions) pulls the score down hard."""
    words = set(content_words(text))
    uniq = list(dict.fromkeys(query_words))
    weight = {w: _idf_of(w, idf, unknown) for w in uniq}
    total = sum(weight.values())
    return sum(weight[w] for w in uniq if _matches(w, words)) / total if total else 0.0


async def hybrid_search(session: AsyncSession, query: str, qvec: list[float] | None, *, top_k: int,
                        min_similarity: float) -> list[Hit]:
    """`qvec` None (the embedding provider is down): words only."""
    version = await embedding_signature(session)
    vec_rows = (await session.execute(
        select(Chunk.id, Chunk.embedding.cosine_distance(qvec).label("d")).join(Document, Chunk.document_id == Document.id)
        .where(Document.status == "ready", Chunk.embedding_version == version).order_by("d").limit(40))).all() \
        if qvec is not None else []
    index = await lexical_index(session)
    lex = index.search(query, 40)
    unknown = max(index.idf.values(), default=1.0)  # a word no document contains is as telling as the rarest one
    ids = list(dict.fromkeys([i for i, _ in vec_rows] + [i for i, _ in lex]))
    if not ids:
        return []
    rows = (await session.execute(
        select(Chunk, Document, (Chunk.embedding.cosine_distance(qvec) if qvec is not None else literal(1.0)).label("d"))
        .join(Document, Chunk.document_id == Document.id).where(Chunk.id.in_(ids)))).all()
    lex_max = max((s for _, s in lex), default=0.0) or 1.0
    lex_by_id = dict(lex)
    qwords = content_words(query)
    hits: list[Hit] = []
    for c, d, dist in rows:
        vec = max(0.0, 1 - float(dist))
        lx = lex_by_id.get(c.id, 0.0) / lex_max
        full = f"{c.question or ''} {c.content}"
        matched, total = coverage(qwords, full)
        wcov = weighted_coverage(qwords, full, index.idf, unknown)
        qcov = weighted_coverage(qwords, c.question, index.idf, unknown) if c.question else 0.0
        # ...and how much of the record's question the guest actually asked (a narrow match beats a broad one)
        qprec = weighted_coverage(content_words(c.question), query, index.idf, unknown) if c.question else 0.0
        qsim = question_similarity(query, c.question)
        # the important words are there, the record's question is close AND shares them, or the admin's vector threshold
        relevant = (enough_coverage(matched, total) and wcov >= 0.55) or (qsim >= 0.75 and wcov >= 0.35) \
            or vec >= min_similarity
        if not relevant:
            continue
        score = (0.2 * vec + 0.2 * lx + 0.35 * wcov + 0.25 * qcov + 0.25 * qprec
                 + (0.3 if qsim >= 0.8 and wcov >= 0.5 else 0.15 if qsim >= 0.65 and wcov >= 0.5 else 0.0))
        hits.append(Hit(c, d, round(score, 4), round(vec, 4), round(lx, 4), matched, total, round(qsim, 3), round(wcov, 3)))
    hits.sort(key=lambda h: -h.score)
    provider = await get_setting(session, "rerank_provider") or "none"
    if provider != "none" and hits:
        candidates = hits[:max(1, int(await get_setting(session, "rerank_candidates") or 20))]
        try:
            ranked, minimum = await rerank_texts(session, query, [h.chunk.content for h in candidates])
            if ranked is not None:
                by_index = {index: score for index, score in ranked if 0 <= index < len(candidates)}
                reranked = []
                for index, hit in enumerate(candidates):
                    if index in by_index and by_index[index] >= minimum:
                        hit.rerank = round(by_index[index], 4)
                        hit.score = hit.rerank
                        reranked.append(hit)
                hits = sorted(reranked, key=lambda h: -h.score)
        except Exception as exc:  # retrieval must remain available when an optional provider is down
            logger.warning("reranker failed; using hybrid scores: %s", exc)
    return hits[:top_k]


# ---- short answers -----------------------------------------------------------------------------------------------
def strip_head(content: str) -> str:
    return _HEAD.sub("", content, count=1)


def shorten(text: str, max_chars: int) -> str:
    """Whole sentences up to max_chars (0 = no limit); a single long sentence is cut at a word boundary."""
    text = re.sub(r"[ \t]+", " ", text.strip())
    if not max_chars or len(text) <= max_chars:
        return text
    out = ""
    for s in (x.strip() for x in _SPLIT_SENTENCES.split(text)):
        if not s:
            continue
        if len(out) + len(s) + 1 > max_chars:
            break
        out = f"{out} {s}".strip()
    if not out:
        out = text[:max_chars].rsplit(" ", 1)[0].rstrip(",;:—-") + "…"
    return out


async def extractive_answer(session: AsyncSession, query: str, hits: list[Hit], max_chars: int) -> str | None:
    """None when the best chunk shares no words with the question: without an AI to judge relevance,
    a vector-only match is not trusted to be quoted back to the guest."""
    top = hits[0]
    if top.weighted < 0.55 and not (top.question_sim >= 0.75 and top.weighted >= 0.35):
        return None  # the important words of the question are not in the text: don't guess
    if top.chunk.question:
        # a Q&A record answers this: give one record's answer, never sentences stitched from different records - and
        # only a record whose own question covers the guest's words ("Quantum Foundation services" must not get a
        # parenting answer that merely mentions Quantum)
        index = await lexical_index(session)
        unknown = max(index.idf.values(), default=1.0)
        qwords = content_words(query)
        for h in hits:
            if not h.chunk.question:
                continue
            if weighted_coverage(qwords, h.chunk.question, index.idf, unknown) < RECORD_QUESTION_MIN \
                    and h.question_sim < 0.75:
                continue
            parts = (await session.execute(select(Chunk.content).where(
                Chunk.document_id == h.chunk.document_id, Chunk.question == h.chunk.question).order_by(Chunk.position))).scalars().all()
            return shorten(_dedupe_overlap([strip_head(p) for p in parts]), max_chars)
        if all(h.chunk.question for h in hits):
            return None  # every candidate is a record about something else: say "don't know" instead
    qwords = list(dict.fromkeys(content_words(query)))
    # rare words decide what the question is about ("ডিম" matters more than "হার্ট ক্লাব", which is everywhere)
    idf = (await lexical_index(session)).idf
    top_idf = max(idf.values(), default=1.0)
    weight = {w: _idf_of(w, idf, top_idf) for w in qwords}
    total_w = sum(weight.values()) or 1.0
    sentences: list[tuple[float, int, str]] = []
    seen: set[str] = set()
    for rank, h in enumerate(hits[:3]):
        if h.chunk.question:  # Q&A records are only quoted whole (above), never mixed into free text
            continue
        for i, s in enumerate(x.strip() for x in _SPLIT_SENTENCES.split(strip_head(h.chunk.content))):
            if len(s) < 3 or s in seen:
                continue
            seen.add(s)
            words = set(content_words(s))
            got = sum(weight[w] for w in qwords if _matches(w, words)) / total_w
            sentences.append((got - rank * 0.1, rank * 10_000 + i, s))
    best = sorted(sentences, key=lambda x: -x[0])[:3]
    best = [b for b in best if b[0] > 0] or best[:1]
    picked = " ".join(s for _, _, s in sorted(best, key=lambda x: x[1]))
    return shorten(picked, max_chars)


def _dedupe_overlap(parts: list[str]) -> str:
    """Chunks of one answer overlap by a few words; glue them back without repeating."""
    out = ""
    for p in parts:
        if not out:
            out = p
            continue
        words = p.split()
        cut = 0
        for k in range(min(40, len(words)), 0, -1):
            if out.endswith(" ".join(words[:k])):
                cut = k
                break
        out = f"{out} {' '.join(words[cut:])}".strip()
    return out
