import hashlib
import hmac


def compute_signature(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(body: bytes, header: str | None, secret: str) -> bool:
    """X-Hub-Signature-256 check. An unset secret or missing header is always a failure, never a bypass."""
    if not secret or not header:
        return False
    return hmac.compare_digest(compute_signature(body, secret), header.strip())
