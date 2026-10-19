"""Reversible, per-request redaction. The placeholder map lives only in the Redactor instance (memory)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.privacy.patterns import (ADDRESS_RE, CARD_CAND_RE, CARD_MAX, CARD_MIN, EMAIL_RE, NID_BARE_RE,
                                  NID_CONTEXT_RE, PHONE_CAND_RE, ascii_digits, digits_only, luhn_ok)

ALL_KINDS = frozenset({"CARD", "NID", "EMAIL", "PHONE", "ADDRESS", "NAME", "CUSTOM"})
SENSITIVE_KINDS = frozenset({"CARD", "NID", "CUSTOM"})  # always redacted, even for allow_pii providers
_PRIORITY = {"CARD": 0, "NID": 1, "CUSTOM": 2, "EMAIL": 3, "PHONE": 4, "ADDRESS": 5, "NAME": 6}
_PLACEHOLDER_RE = re.compile(r"\[([A-Z]+)_(\d+)\]")


def _normalized_phone(digits: str) -> str | None:
    from app.services.contacts import normalize_phone  # local import: contacts -> patterns, no cycle at import time
    return normalize_phone(digits)


def _is_phone(digits: str) -> bool:
    """8801812345604 (a +880 number) can pass the card checksum: a valid phone number is never a card."""
    from app.services.contacts import normalize_phone  # local import: contacts -> patterns, no cycle at import time
    return normalize_phone(digits) is not None


@dataclass(frozen=True)
class Finding:
    kind: str
    placeholder: str  # never contains the original value


@dataclass
class _Span:
    start: int
    end: int
    kind: str
    key: str  # canonical value used for dedup (same value -> same placeholder)


class Redactor:
    def __init__(self, kinds: frozenset[str] | set[str] = ALL_KINDS, known_names: tuple[str, ...] = (),
                 name_mode: str = "placeholder", custom_patterns: tuple[str, ...] = (),
                 keep: frozenset[str] = frozenset()):
        self.kinds = frozenset(kinds)
        self._keep = keep  # public contacts left as they are: our own office numbers/emails (E.164 / lower-case)
        self.name_mode = name_mode  # placeholder | first_name | none
        self._custom = [re.compile(p) for p in custom_patterns]
        self._map: dict[str, str] = {}          # placeholder -> original
        self._rev: dict[tuple[str, str], str] = {}
        self._counts: dict[str, int] = {}
        self.findings: list[Finding] = []
        self._name_parts: list[str] = []
        self._first_name: str | None = None
        for n in known_names:
            self.add_name(n)

    # ---- names -------------------------------------------------------------------------
    def add_name(self, full_name: str | None) -> None:
        if not full_name or not full_name.strip():
            return
        parts = [p for p in re.split(r"\s+", full_name.strip()) if len(p) >= 2]
        if not parts:
            return
        if self._first_name is None:
            self._first_name = parts[0]
        for p in [full_name.strip(), *parts]:
            if p not in self._name_parts:
                self._name_parts.append(p)
        self._name_parts.sort(key=len, reverse=True)

    def name_for_prompt(self, full_name: str | None) -> str | None:
        """How a profile name may appear in a prompt, according to name_mode."""
        if not full_name:
            return None
        self.add_name(full_name)
        if self.name_mode == "none":
            return None
        if self.name_mode == "first_name":
            return full_name.strip().split()[0]
        return self._placeholder("NAME", "name")

    # ---- core --------------------------------------------------------------------------
    def _placeholder(self, kind: str, key: str) -> str:
        if kind == "NAME":
            key = "name"  # one canonical NAME placeholder, restores to the first name
        existing = self._rev.get((kind, key))
        if existing:
            return existing
        n = self._counts.get(kind, 0) + 1
        self._counts[kind] = n
        ph = f"[{kind}_{n}]"
        self._rev[(kind, key)] = ph
        self.findings.append(Finding(kind, ph))
        return ph

    def _detect(self, norm: str, names: bool = True) -> list[_Span]:
        spans: list[_Span] = []
        if "CARD" in self.kinds:
            for m in CARD_CAND_RE.finditer(norm):
                d = digits_only(m.group())
                if CARD_MIN <= len(d) <= CARD_MAX and luhn_ok(d) and not _is_phone(d):
                    spans.append(_Span(m.start(), m.end(), "CARD", d))
        if "NID" in self.kinds:
            for m in NID_CONTEXT_RE.finditer(norm):
                spans.append(_Span(m.start(1), m.end(1), "NID", digits_only(m.group(1))))
            for m in NID_BARE_RE.finditer(norm):
                spans.append(_Span(m.start(), m.end(), "NID", m.group()))
        if "CUSTOM" in self.kinds:
            for rx in self._custom:
                for m in rx.finditer(norm):
                    if m.end() > m.start():
                        spans.append(_Span(m.start(), m.end(), "CUSTOM", m.group()))
        if "EMAIL" in self.kinds:
            for m in EMAIL_RE.finditer(norm):
                if m.group().lower() not in self._keep:
                    spans.append(_Span(m.start(), m.end(), "EMAIL", m.group().lower()))
        if "PHONE" in self.kinds:
            for m in PHONE_CAND_RE.finditer(norm):
                d = digits_only(m.group())
                if 8 <= len(d) <= 15 and not (self._keep and _normalized_phone(d) in self._keep):
                    spans.append(_Span(m.start(), m.end(), "PHONE", d))
        if "ADDRESS" in self.kinds:
            for m in ADDRESS_RE.finditer(norm):
                spans.append(_Span(m.start(), m.end(), "ADDRESS", m.group().lower()))
        if names and "NAME" in self.kinds and self.name_mode != "none":
            for part in self._name_parts:
                for m in re.finditer(rf"(?<!\w){re.escape(part)}(?!\w)", norm, flags=re.IGNORECASE):
                    # first_name mode: the first name may pass; surnames / full names are hidden
                    if self.name_mode == "first_name" and m.group().lower() == (self._first_name or "").lower():
                        continue
                    spans.append(_Span(m.start(), m.end(), "NAME", "name"))
        return spans

    def redact(self, text: str, *, names: bool = True) -> str:
        """names=False for business content (knowledge chunks): a customer called 'Rose' must not turn the
        word 'rose' in a flower-shop price list into a placeholder."""
        if not text:
            return text
        norm = ascii_digits(text)
        spans = sorted(self._detect(norm, names), key=lambda s: (_PRIORITY[s.kind], s.start, -(s.end - s.start)))
        taken: list[_Span] = []
        for s in spans:
            if all(s.end <= t.start or s.start >= t.end for t in taken):
                taken.append(s)
        out, last = [], 0
        for s in sorted(taken, key=lambda s: s.start):
            out.append(text[last:s.start])
            ph = self._placeholder(s.kind, s.key)
            if ph not in self._map:
                original = text[s.start:s.end]
                if s.kind == "NAME":
                    original = self._first_name or original
                self._map[ph] = original
            out.append(ph)
            last = s.end
        out.append(text[last:])
        return "".join(out)

    def restore(self, text: str) -> str:
        """Put real values back. Unknown placeholders (hallucinated by the model) are stripped."""
        def sub(m: re.Match) -> str:
            return self._map.get(m.group(0), "")
        return _PLACEHOLDER_RE.sub(sub, text)

    @property
    def mapping_values(self) -> set[str]:
        return set(self._map.values())

    def summary(self) -> list[dict]:
        """Safe-to-display description of what was redacted (no original values)."""
        return [{"kind": f.kind, "placeholder": f.placeholder} for f in self.findings]


STORAGE_KINDS = frozenset({"CARD", "NID", "EMAIL", "PHONE", "ADDRESS", "CUSTOM"})  # names stay readable in the inbox


def redact_for_storage(text: str, custom_patterns: tuple[str, ...] = (), keep: frozenset[str] = frozenset()) -> str:
    """Irreversible redaction for what we persist in `messages.content_redacted`. `keep`: our own public contacts
    (contact_owner.business_contacts), which stay readable, so a later prompt's history still has the real number."""
    return Redactor(kinds=STORAGE_KINDS, custom_patterns=custom_patterns, keep=keep).redact(text)
