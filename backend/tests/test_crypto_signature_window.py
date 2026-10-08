from datetime import datetime, timedelta, timezone

import pytest

from app.channels.signature import compute_signature, verify_signature
from app.channels.window import window_state
from app.core import crypto

NOW = datetime(2026, 1, 10, 12, 0, tzinfo=timezone.utc)


def test_signature_ok_and_tamper():
    body = b'{"a":1}'
    sig = compute_signature(body, "s3cret")
    assert verify_signature(body, sig, "s3cret")
    assert not verify_signature(body + b" ", sig, "s3cret")
    assert not verify_signature(body, sig, "other")


def test_signature_missing_header_or_secret_never_passes():
    assert not verify_signature(b"x", None, "s")
    assert not verify_signature(b"x", "", "s")
    assert not verify_signature(b"x", compute_signature(b"x", ""), "")


def test_encrypt_roundtrip_and_aad_binding():
    token = crypto.encrypt("+8801712345678", aad="phone")
    assert "8801712345678" not in token
    assert crypto.decrypt(token, aad="phone") == "+8801712345678"
    with pytest.raises(Exception):
        crypto.decrypt(token, aad="email")  # ciphertext bound to its purpose


def test_encrypt_is_randomized_but_blind_index_is_stable():
    assert crypto.encrypt("x") != crypto.encrypt("x")
    assert crypto.blind_index("a@b.com", "email") == crypto.blind_index("a@b.com", "email")
    assert crypto.blind_index("a@b.com", "email") != crypto.blind_index("a@b.com", "phone")


@pytest.mark.parametrize("ch", ["messenger", "instagram", "whatsapp"])
def test_window_boundaries(ch):
    assert window_state(ch, NOW - timedelta(hours=23, minutes=59), now=NOW).open
    assert not window_state(ch, NOW - timedelta(hours=24, minutes=1), now=NOW).open
    assert not window_state(ch, None, now=NOW).open


def test_whatsapp_outside_window_needs_template():
    ws = window_state("whatsapp", NOW - timedelta(days=2), now=NOW)
    assert not ws.open and ws.needs_template


def test_human_agent_tag_only_for_humans_and_when_enabled():
    last = NOW - timedelta(days=3)
    assert not window_state("messenger", last, now=NOW, sender_is_human=False, human_agent_enabled=True).open
    assert not window_state("messenger", last, now=NOW, sender_is_human=True, human_agent_enabled=False).open
    ws = window_state("messenger", last, now=NOW, sender_is_human=True, human_agent_enabled=True)
    assert ws.open and ws.needs_human_agent_tag
    assert not window_state("messenger", NOW - timedelta(days=8), now=NOW, sender_is_human=True, human_agent_enabled=True).open
    assert not window_state("whatsapp", last, now=NOW, sender_is_human=True, human_agent_enabled=True).open
