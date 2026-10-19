"""Card details are never asked for, by the AI or by staff, and a card number a guest sends raises an alert.

- asks_for_card(text): a reply that mentions card number / CVV, or asks to send / share an OTP / PIN / expiry,
  without saying "don't share".
  The AI's reply is replaced (and flagged); a staff reply is refused.
- contains_card(text): a 13-19 digit number with a valid card checksum that is not a phone number. The message is
  already stored hidden ([CARD_1]); it is flagged, the guest is warned, and an alert goes out.
Flagged messages show in the Inbox, in System health and as a webhook alert (monitoring.check_alerts).
"""
from __future__ import annotations

import re

from app.privacy.patterns import CARD_CAND_RE, CARD_MAX, CARD_MIN, ascii_digits, digits_only, luhn_ok
from app.services.contacts import normalize_phone

CARD_SHARED = "card_shared"            # a guest sent a card number
CARD_REQUEST_BLOCKED = "card_request"  # the AI asked for card details: reply replaced
FLAGS = (CARD_SHARED, CARD_REQUEST_BLOCKED)

_CARD_TERMS = re.compile(
    r"(?i)(credit|debit|visa|master\s*card|amex|bank)\s*card|card\s*(number|no\.?|details|info)|\bcvv\b|\bcvc\b|"
    r"কার্ড(ের)?\s*(নম্বর|নাম্বার|তথ্য|মেয়াদ)|ক্রেডিট\s*কার্ড|ডেবিট\s*কার্ড|সিভিভি")
# An OTP / PIN / expiry is also part of normal guidance ("verify the OTP on your phone" while filling in the official
# registration form); it is a request only when the same sentence asks the guest to send, share, tell or give it.
_SECRET_TERMS = re.compile(r"(?i)\bpin\b|\botp\b|expiry|expiration|পিন|ওটিপি")
_HAND_OVER = re.compile(
    r"(?i)\b(send|share|tell|give|provide|reply|forward|pathan|pathiye|din|diben|janan|bolun|likhun)\b|"
    r"পাঠান|পাঠাবেন|পাঠিয়ে|দিন|দেবেন|দিবেন|দিয়ে\s*দিন|জানান|জানাবেন|বলুন|বলবেন|লিখুন|লিখে\s*পাঠান|শেয়ার")
_SENTENCES = re.compile(r"[।.!?\n]+")
_NEGATION = re.compile(
    r"(?i)\b(never|don'?t|do\s+not|won'?t|will\s+not|no\s+need|not\s+required)\b|কখনো|কখনোই|দেবেন\s*না|দিবেন\s*না|"
    r"শেয়ার\s*করবেন\s*না|\S+বেন\s*না|চাই\s*না|চাইবো\s*না|চাইব\s*না|প্রয়োজন\s*নেই|লাগবে\s*না|dibe?n\s*na|kokhono")


def asks_for_card(text: str) -> bool:
    text = text or ""
    if _NEGATION.search(text):
        return False
    return bool(_CARD_TERMS.search(text)) or any(
        _SECRET_TERMS.search(s) and _HAND_OVER.search(s) for s in _SENTENCES.split(text))


def contains_card(text: str) -> bool:
    for m in CARD_CAND_RE.finditer(ascii_digits(text or "")):
        d = digits_only(m.group())
        if CARD_MIN <= len(d) <= CARD_MAX and luhn_ok(d) and not normalize_phone(d):
            return True
    return False
