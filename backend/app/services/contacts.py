"""Phone/email extraction and normalization."""
from __future__ import annotations

import phonenumbers

from app.core.config import get_settings
from app.privacy.patterns import EMAIL_RE, PHONE_CAND_RE, ascii_digits


def normalize_phone(raw: str, region: str | None = None) -> str | None:
    region = region or get_settings().default_phone_region
    s = ascii_digits(raw).strip()
    try:
        num = phonenumbers.parse(s, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def normalize_email(raw: str) -> str | None:
    m = EMAIL_RE.fullmatch(raw.strip())
    return raw.strip().lower() if m else None


def extract_phones(text: str, region: str | None = None) -> list[str]:
    seen: list[str] = []
    for m in PHONE_CAND_RE.finditer(ascii_digits(text)):
        n = normalize_phone(m.group(), region)
        if n and n not in seen:
            seen.append(n)
    return seen


def extract_emails(text: str) -> list[str]:
    seen: list[str] = []
    for m in EMAIL_RE.finditer(text):
        e = m.group().lower()
        if e not in seen:
            seen.append(e)
    return seen
