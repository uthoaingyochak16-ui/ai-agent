"""AES-GCM encryption for data at rest + HMAC blind indexes for lookup."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings

_PREFIX = "v1:"


def _aes() -> AESGCM:
    return AESGCM(base64.b64decode(get_settings().encryption_key))


def encrypt(plaintext: str, aad: str = "") -> str:
    nonce = os.urandom(12)
    ct = _aes().encrypt(nonce, plaintext.encode(), aad.encode() or None)
    return _PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt(token: str, aad: str = "") -> str:
    if not token.startswith(_PREFIX):
        raise ValueError("unknown ciphertext version")
    raw = base64.b64decode(token[len(_PREFIX):])
    nonce, ct = raw[:12], raw[12:]
    return _aes().decrypt(nonce, ct, aad.encode() or None).decode()


def blind_index(value: str, purpose: str) -> str:
    """Deterministic keyed hash for equality lookup (phone/email)."""
    key = get_settings().hmac_key.encode()
    return hmac.new(key, f"{purpose}:{value}".encode(), hashlib.sha256).hexdigest()
